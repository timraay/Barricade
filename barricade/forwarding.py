import asyncio
import logging
from typing import Sequence
from cachetools import TTLCache
import discord
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from barricade import schemas
from barricade.constants import DISCORD_PC_REPORTS_CHANNEL_ID, T17_SUPPORT_CUTOFF_DATE, T17_SUPPORT_DISCORD_CHANNEL_ID, T17_SUPPORT_NUM_ALLOWED_REJECTS, T17_SUPPORT_NUM_REQUIRED_RESPONSES, T17_SUPPORT_REASON_MASK
from barricade.crud.communities import get_community_by_id
from barricade.crud.reports import get_report_by_id, get_report_message_by_community_id, is_player_reported
from barricade.crud.responses import bulk_get_response_stats, get_community_responses_to_report, get_pending_responses, get_reports_for_player_with_no_community_review
from barricade.db import models, session_factory
from barricade.discord import bot
from barricade.discord.communities import get_alerts_channel, get_alerts_role_mention, get_confirmations_channel, get_forward_channel
from barricade.discord.reports import get_alert_embed, get_report_channel, get_report_embed, get_t17_support_forward_channel
from barricade.discord.views.player_review import PlayerReviewView
from barricade.discord.views.report_management import ReportManagementView
from barricade.discord.views.t17_support_player_review import T17SupportPlayerReviewView
from barricade.enums import Platform, ReportMessageType
from barricade.hooks import EventHooks, add_hook
from barricade.integrations.manager import IntegrationManager
from barricade.logger import get_logger
from barricade.urls import URLFactory

@add_hook(EventHooks.report_create)
async def forward_report_to_communities(report: schemas.ReportWithToken):
    async with session_factory.begin() as db:
        stmt = select(models.Community).where(
            models.Community.forward_guild_id.is_not(None),
            models.Community.forward_channel_id.is_not(None),
            models.Community.id != report.token.community_id,
            or_(
                models.Community.reasons_filter.is_(None),
                models.Community.reasons_filter.bitwise_and(report.reasons_bitflag) != 0,
            )
        )
        if report.token.platform == Platform.PC:
            stmt = stmt.where(models.Community.is_pc.is_(True))
        elif report.token.platform == Platform.CONSOLE:
            stmt = stmt.where(models.Community.is_console.is_(True))

        result = await db.scalars(stmt)
        db_communities = result.all()

        if not db_communities:
            return

        for db_community in db_communities:
            try:
                community = schemas.CommunityRef.model_validate(db_community)
                
                # Create pending responses
                responses = [schemas.PendingResponse(
                    pr_id=player.id,
                    community_id=community.id,
                    player_report=player,
                    community=community
                ) for player in report.players]

                await send_or_edit_report_review_message(report, responses, community)

            except:
                logger = get_logger(db_community.id)
                logger.exception("Failed to forward %r to %r", report, db_community)

@add_hook(EventHooks.report_create)
async def forward_report_to_token_owner(report: schemas.ReportWithToken):
    await send_or_edit_report_management_message(report)

@add_hook(EventHooks.report_edit)
async def edit_public_report_message(report: schemas.ReportWithRelations, _):
    try:
        embed = await get_report_embed(report)
        channel = get_report_channel(report.token.platform)
        message = bot.get_partial_message(channel.id, report.message_id, channel.guild.id)
        await message.edit(embed=embed)
    except discord.HTTPException:
        pass

@add_hook(EventHooks.report_edit)
async def edit_private_report_messages(report: schemas.ReportWithRelations, _):
    if not report.messages:
        return
    
    async with session_factory() as db:
        for message_data in report.messages:
            try:
                # Get new message content
                if message_data.message_type == ReportMessageType.MANAGE:
                    await send_or_edit_report_management_message(report)
                elif message_data.message_type == ReportMessageType.REVIEW:
                    if not message_data.community_id:
                        logging.error("Report message has type REVIEW but is missing community id")
                        continue

                    # Create pending responses
                    db_community = await get_community_by_id(db, message_data.community_id)
                    community = schemas.Community.model_validate(db_community)

                    responses = await get_pending_responses(db, community, report.players)
                    await send_or_edit_report_review_message(report, responses, community)
                elif message_data.message_type == ReportMessageType.T17_SUPPORT:
                    await send_or_edit_t17_support_report_review_message(report)
                else:
                    raise ValueError("Unknown message type \"%s\"" % message_data.message_type)
            except:
                logger = get_logger(message_data.community_id) if message_data.community_id else logging
                logger.exception("Unexpected error occurred while attempting to edit %r", message_data)

@add_hook(EventHooks.report_delete)
async def delete_public_report_message(report: schemas.ReportWithRelations):
    try:
        channel = get_report_channel(report.token.platform)
        message = bot.get_partial_message(channel.id, report.message_id, channel.guild.id)
        await message.delete()
    except discord.HTTPException:
        pass

@add_hook(EventHooks.report_delete)
async def delete_private_report_messages(report: schemas.ReportWithRelations):
    async with session_factory() as db:
        for message_data in report.messages:
            try:
                message = bot.get_partial_message(message_data.channel_id, message_data.message_id)

                # Send warning if community had banned this player
                if message_data.message_type == ReportMessageType.REVIEW and message_data.community_id:
                    db_responses = await get_community_responses_to_report(db, report, message_data.community_id)
                    if any(db_response.banned for db_response in db_responses):
                        await message.edit(view=None)
                        await message.reply(
                            embed=discord.Embed(
                                description="-# **This report was deleted!** One or more bans have been revoked as a result.",
                                color=discord.Colour.red()
                            )
                        )
                        continue
                
                # Send warning if T17 Support was notified about this player
                elif message_data.message_type == ReportMessageType.T17_SUPPORT:
                    await message.edit(view=None)
                    await message.reply(
                        embed=discord.Embed(
                            description="-# **This report was deleted!** If this user was game banned, consider revoking it.",
                            color=discord.Colour.red()
                        )
                    )
                    continue
                
                # Otherwise: Simply delete the report
                await message.delete()
            except discord.HTTPException:
                pass
            except:
                logger = get_logger(message_data.community_id) if message_data.community_id else logging
                logger.exception("Unexpected error occurred while attempting to delete %r", message_data)


# Integration NEW_REPORT hook

@add_hook(EventHooks.report_create)
async def process_integration_report_create_hooks(report: schemas.ReportWithToken):
    await invoke_integration_report_create_hook(report)

@add_hook(EventHooks.report_edit)
async def process_integration_report_edit_hooks(report: schemas.ReportWithRelations, _):
    await invoke_integration_report_create_hook(report)

async def invoke_integration_report_create_hook(report: schemas.ReportWithToken):
    manager = IntegrationManager()
    await asyncio.gather(*[
        integration.on_report_create(report)
        for integration in manager.get_all()
        if integration.config.enabled
    ])


# Report URL Cache

@add_hook(EventHooks.report_create)
async def remove_token_url_from_cache(report: schemas.ReportWithToken):
    URLFactory.remove(report.token)


# Player Alerts

__is_player_reported = TTLCache[str, bool](maxsize=9999, ttl=60*10)

async def send_alert_to_community_for_unreviewed_players(community_id: int, player_ids: Sequence[str]) -> dict | None:
    reported_player_ids: list[str] = []
    
    async with session_factory() as db:
        # Go over all players to check whether they have been reported
        for player_id in player_ids:
            # First look for a cached response, otherwise fetch from DB
            cache_hit = __is_player_reported.get(player_id)
            if cache_hit is not None:
                if cache_hit:
                    reported_player_ids.append(player_id)
            else:
                is_reported = await is_player_reported(db, player_id)
                __is_player_reported[player_id] = is_reported
                if is_reported:
                    reported_player_ids.append(player_id)

        if reported_player_ids:
            # There are one or more players that have reports
            db_community = await get_community_by_id(db, community_id)
            community = schemas.CommunityRef.model_validate(db_community)

            channel = get_alerts_channel(community)
            if not channel:
                # We have nowhere to send the alert, so we just ignore
                return

            for player_id in reported_player_ids:
                # For each player, get all reports that this community has not yet responded to
                db_reports = await get_reports_for_player_with_no_community_review(
                    db, player_id, community_id, community.reasons_filter
                )

                messages: list[discord.Message] = []
                sorted_reports = sorted(
                    (schemas.ReportWithToken.model_validate(db_report) for db_report in db_reports),
                    key=lambda x: x.created_at
                )

                # Locate all the messages, resending as necessary, and updating them with the most
                # up-to-date details.
                for report in sorted_reports:
                    db_community = await get_community_by_id(db, community.id)
                    responses = await get_pending_responses(db, community, report.players)

                    stats = await bulk_get_response_stats(db, report.players)

                    if get_forward_channel(community):
                        message = await send_or_edit_report_review_message(report, responses, community, stats=stats)

                    else:
                        view = PlayerReviewView(responses=responses)
                        embed = await PlayerReviewView.get_embed(report, responses, stats=stats)
                        message = await channel.send(embed=embed, view=view)
                    
                    if message:
                        # Remember the message
                        messages.append(message)

                if not messages:
                    # No messages were located, so we don't have any reports to point the user at.
                    continue

                # Get the most recent PlayerReport for the most up-to-date name
                player = next(
                    pr for pr in sorted_reports[-1].players
                    if pr.player_id == player_id
                )

                mention = await get_alerts_role_mention(community)
                if mention:
                    content = f"{mention} a potentially dangerous player has joined your server!"
                else:
                    content = "A potentially dangerous player has joined your server!"

                reports_urls = list(zip(sorted_reports, (message.jump_url for message in messages)))
                embed = get_alert_embed(
                    reports_urls=list(reversed(reports_urls)),
                    player=player
                )

                await channel.send(
                    content=content,
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions(roles=True),
                )

# Forward to T17 Support

def should_forward_to_staff(report: schemas.ReportWithToken, stats: dict[int, schemas.ResponseStats]) -> bool:
    if (
        (report.reasons_bitflag & T17_SUPPORT_REASON_MASK) != 0
        and (
            not T17_SUPPORT_CUTOFF_DATE
            or report.created_at >= T17_SUPPORT_CUTOFF_DATE
        )
    ):
        for stat in stats.values():
            num_responses = stat.num_banned + stat.num_rejected
            if (
                num_responses >= T17_SUPPORT_NUM_REQUIRED_RESPONSES
                and stat.num_rejected <= T17_SUPPORT_NUM_ALLOWED_REJECTS
            ):
                return True
    
    return False

if T17_SUPPORT_DISCORD_CHANNEL_ID:
    @add_hook(EventHooks.player_ban)
    async def send_cheating_report_to_staff(response: schemas.ResponseWithToken):
        channel = get_t17_support_forward_channel()
        if not channel:
            return

        async with session_factory() as db:
            db_report = await get_report_by_id(db, response.player_report.report_id, load_token=True)
            report = schemas.ReportWithToken.model_validate(db_report)
            stats = await bulk_get_response_stats(db, report.players)

            if should_forward_to_staff(report, stats):
                await send_or_edit_t17_support_report_review_message(report, stats=stats)


# Utility methods

async def send_or_edit_report_review_message(
    report: schemas.ReportWithToken,
    responses: list[schemas.PendingResponse],
    community: schemas.CommunityRef,
    stats: dict[int, schemas.ResponseStats] | None = None,
):
    if report.token.community_id == community.id:
        # Since the community created the report, they should not
        # be able to review it.
        raise ValueError("Report owner should not be able to review their own report")
    
    async with session_factory.begin() as db:
        view = PlayerReviewView(responses=responses)
        embed = await PlayerReviewView.get_embed(report, responses, stats=stats)
        return await send_or_edit_message(
            db,
            report=report,
            community=community,
            message_type=ReportMessageType.REVIEW,
            channel=get_forward_channel(community),
            embed=embed,
            view=view,
        )

async def send_or_edit_report_management_message(
    report: schemas.ReportWithToken,
):
    community = report.token.community
    admin = report.token.admin

    user = await bot.get_or_fetch_member(admin.discord_id)
    content = f"{user.mention} your report was submitted! (ID: #{report.id})"
    
    async with session_factory.begin() as db:                    
        view = ReportManagementView(report)
        embed = await ReportManagementView.get_embed(report)
        return await send_or_edit_message(
            db,
            report=report,
            community=community,
            message_type=ReportMessageType.MANAGE,
            channel=get_confirmations_channel(community),
            embed=embed,
            view=view,
            admin=admin,
            content=content,
            allowed_mentions=discord.AllowedMentions(users=[user])
        )

async def send_or_edit_t17_support_report_review_message(
    report: schemas.ReportWithToken,
    stats: dict[int, schemas.ResponseStats] | None = None,
):
    async with session_factory.begin() as db:
        view = T17SupportPlayerReviewView(report)
        embed = await T17SupportPlayerReviewView.get_embed(report, stats=stats)
        return await send_or_edit_message(
            db,
            report=report,
            community=None,
            message_type=ReportMessageType.T17_SUPPORT,
            channel=get_t17_support_forward_channel(),
            embed=embed,
            view=view,
        )

async def send_or_edit_message(
    db: AsyncSession,
    report: schemas.ReportRef,
    community: schemas.CommunityRef | None,
    message_type: ReportMessageType,
    channel: discord.TextChannel | None,
    embed: discord.Embed,
    view: discord.ui.View,
    content: str | None = None,
    admin: schemas.AdminRef | None = None,
    allowed_mentions: discord.AllowedMentions = discord.AllowedMentions.none()
):
    if community:
        logger = get_logger(community.id)
        community_id = community.id
    else:
        logger = logging
        community_id = None

    db_message = await get_report_message_by_community_id(db, report.id, community_id)
    # If this was already sent before, try editing first
    if db_message:
        if db_message.message_type != message_type:
            logger.warning(
                'Found existing message %s with type %s but expected %s',
                db_message.message_id, db_message.message_type, message_type,
            )

        # Get existing message
        message = bot.get_partial_message(db_message.channel_id, db_message.message_id)
        try:
            # Edit the message
            message = await message.edit(content=content, embed=embed, view=view, allowed_mentions=allowed_mentions)
            return message
        except discord.NotFound:
            # The message no longer exists. Remove record and send a new one.
            await db.delete(db_message)

    message = None
    if channel:
        try:
            # Send message
            message = await channel.send(content=content, embed=embed, view=view, allowed_mentions=allowed_mentions)
        except discord.HTTPException as e:
            logger.error(
                "Failed to send message to %s/%s. %s: %s",
                channel.guild.id, channel.id, type(e).__name__, e
            )
    else:
        if community:
            logger.warning("Forward channel %s/%s could not be found", community.forward_guild_id, community.forward_channel_id)
        else:
            logger.warning("Forward channel could not be found")

    if admin and not message:
        # Could not send message to channel, try sending directly to admin instead
        try:
            user = await bot.get_or_fetch_member(admin.discord_id)
            message = await user.send(content=content, embed=embed, view=view, allowed_mentions=allowed_mentions)
        except discord.HTTPException:
            logger.error("Could not send report message to %s (ID: %s)", admin.name, admin.discord_id)

    if message:
        # Add message to database
        message_data = schemas.ReportMessageCreateParams(
            report_id=report.id,
            community_id=community_id,
            channel_id=message.channel.id,
            message_id=message.id,
            message_type=message_type,
        )

        db_message = models.ReportMessage(**message_data.model_dump())
        db.add(db_message)
        await db.flush()
        return message
    