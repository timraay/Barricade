import discord
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from barricade import schemas
from barricade.constants import DISCORD_REPORTS_CHANNEL_ID
from barricade.crud.communities import get_community_by_id
from barricade.crud.responses import get_pending_responses
from barricade.db import models, session_factory
from barricade.discord import bot
from barricade.discord.communities import get_forward_channel
from barricade.discord.reports import get_report_embed
from barricade.discord.views.player_review import PlayerReviewView
from barricade.discord.views.report_management import ReportManagementView
from barricade.hooks import EventHooks, add_hook

@add_hook(EventHooks.report_create)
async def forward_report_to_communities(report: schemas.ReportWithToken):
    async with session_factory.begin() as db:
        stmt = select(models.Community).where(
            models.Community.forward_guild_id.is_not(None),
            models.Community.forward_channel_id.is_not(None),
        ).where(
            models.Community.id != report.token.community_id
        )
        result = await db.scalars(stmt)
        communities = result.all()

        if not communities:
            return

        for community in communities:
            try:
                if not get_forward_channel(community):
                    return
                
                # Create pending responses
                responses = [schemas.PendingResponse(
                    pr_id=player.id,
                    community_id=community.id,
                    player_report=player,
                    community=community
                ) for player in report.players]

                await send_or_edit_report_review_message(report, responses, community)

            except:
                logging.exception("Failed to forward %r to %r", report, community)

@add_hook(EventHooks.report_create)
async def forward_report_to_token_owner(report: schemas.ReportWithToken):
    await send_or_edit_report_management_message(report)

@add_hook(EventHooks.report_edit)
async def edit_public_report_message(report: schemas.ReportWithRelations, _):
    try:
        embed = await get_report_embed(report)
        message = bot.get_partial_message(DISCORD_REPORTS_CHANNEL_ID, report.message_id)
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
                if message_data.community_id == report.token.community_id:
                    await send_or_edit_report_management_message(report)
                else:
                    # Create pending responses
                    community = await get_community_by_id(db, message_data.community_id)
                    responses = await get_pending_responses(db, community, report.players)
                    await send_or_edit_report_review_message(report, responses, community)
            except:
                logging.exception("Unexpected error occurred while attempting to edit %r", message_data)

@add_hook(EventHooks.report_delete)
async def delete_public_report_message(report: schemas.ReportWithRelations):
    try:
        message = bot.get_partial_message(DISCORD_REPORTS_CHANNEL_ID, report.message_id)
        await message.delete()
    except discord.HTTPException:
        pass

@add_hook(EventHooks.report_delete)
async def delete_private_report_messages(report: schemas.ReportWithRelations):
    for message_data in report.messages:
        try:
            message = bot.get_partial_message(message_data.channel_id, message_data.message_id)
            await message.delete()
        except discord.HTTPException:
            pass
        except:
            logging.exception("Unexpected error occurred while attempting to delete %r", message_data)


async def send_or_edit_report_review_message(
    report: schemas.ReportWithToken,
    responses: list[schemas.PendingResponse],
    community: schemas.CommunityRef,
    stats: dict[str, schemas.ResponseStats] = None,
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
            embed=embed,
            view=view,
        )

async def send_or_edit_report_management_message(
    report: schemas.ReportWithToken,
):
    community = report.token.community
    admin = report.token.admin

    user = await bot.get_or_fetch_member(admin.discord_id)
    content=f"{user.mention} your report was submitted! (ID: #{report.id})"
    
    async with session_factory.begin() as db:                    
        view = ReportManagementView(report)
        embed = await ReportManagementView.get_embed(report)
        return await send_or_edit_message(
            db,
            report=report,
            community=community,
            embed=embed,
            view=view,
            admin=admin,
            content=content,
        )

async def send_or_edit_message(
    db: AsyncSession,
    report: schemas.ReportRef,
    community: schemas.CommunityRef,
    embed: discord.Embed,
    view: discord.ui.View,
    content: str | None = None,
    admin: schemas.AdminRef = None
):
    db_message = await db.get(models.ReportMessage, (report.id, community.id))
    # If this was already sent before, try editing first
    if db_message:
        # Get existing message
        message = bot.get_partial_message(db_message.channel_id, db_message.message_id)
        try:
            # Edit the message
            message = await message.edit(content=content, embed=embed, view=view)
            return message
        except discord.NotFound:
            # The message no longer exists. Remove record and send a new one.
            await db.delete(db_message)

    message = None
    channel = get_forward_channel(community)
    if channel:
        try:
            # Send message
            message = await channel.send(content=content, embed=embed, view=view)
        except discord.HTTPException:
            pass

    if admin and not message:
        # Could not send message to channel, try sending directly to admin instead
        try:
            user = await bot.get_or_fetch_member(admin.discord_id)
            message = await user.send(content=content, embed=embed, view=view)
        except discord.HTTPException:
            logging.error("Could not send report message to %s (ID: %s)", admin.name, admin.discord_id)

    if message:
        # Add message to database
        message_data = schemas.ReportMessageCreateParams(
            report_id=report.id,
            community_id=community.id,
            channel_id=message.channel.id,
            message_id=message.id,
        )

        db_message = models.ReportMessage(**message_data.model_dump())
        db.add(db_message)
        await db.flush()
        return message
    