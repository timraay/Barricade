import discord
import logging
from sqlalchemy import select

from bunker import schemas
from bunker.constants import DISCORD_REPORTS_CHANNEL_ID
from bunker.crud.communities import get_community_by_id
from bunker.crud.responses import get_pending_responses
from bunker.db import models, session_factory
from bunker.discord import bot
from bunker.discord.communities import get_forward_channel
from bunker.discord.reports import get_report_embed
from bunker.discord.views.player_review import PlayerReviewView
from bunker.discord.views.report_management import ReportManagementView
from bunker.hooks import EventHooks, add_hook

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
                channel = get_forward_channel(community)
                if not channel:
                    return
                
                # Create pending responses
                responses = [schemas.PendingResponse(
                    pr_id=player.id,
                    community_id=community.id,
                    player_report=player,
                    community=community
                ) for player in report.players]

                # Send message
                view = PlayerReviewView(responses=responses)
                embed = await PlayerReviewView.get_embed(report, responses)
                message = await channel.send(embed=embed, view=view)

                # Add message to database
                message_data = schemas.ReportMessageCreateParams(
                    report_id=report.id,
                    community_id=community.id,
                    channel_id=message.guild.id,
                    message_id=message.guild.id,
                )
                db_message = models.ReportMessage(**message_data.model_dump())
                db.add(db_message)

            except:
                logging.exception("Failed to forward %r to %r", report, community)

@add_hook(EventHooks.report_create)
async def forward_report_to_token_owner(report: schemas.ReportWithToken):
    community = report.token.community
    admin = report.token.admin

    embed = await ReportManagementView.get_embed(report)
    view = ReportManagementView(report)

    user = await bot.get_or_fetch_user(admin.discord_id)
    message = None

    if community.forward_channel_id:
        channel = get_forward_channel(community)
        if channel:
            try:
                message = await channel.send(
                    content=f"{user.mention} your report was submitted! (ID: #{report.id})",
                    embed=embed,
                    view=view,
                )
            except discord.HTTPException:
                pass
    
    if not message:
        try:
            message = await user.send(
                content=user.mention,
                embed=embed,
            )
        except discord.errors.HTTPException:
            logging.error("Could not send report confirmation to %s (ID: %s)", admin.name, admin.discord_id)
    
    if message:
        async with session_factory.begin() as db:
            # Add message to database
            message_data = schemas.ReportMessageCreateParams(
                report_id=report.id,
                community_id=community.id,
                channel_id=message.channel.id,
                message_id=message.id,
            )
            db_message = models.ReportMessage(**message_data.model_dump())
            db.add(db_message)

@add_hook(EventHooks.report_edit)
async def edit_public_report_message(report: schemas.ReportWithRelations):
    try:
        embed = await get_report_embed(report)
        message = bot.get_partial_message(DISCORD_REPORTS_CHANNEL_ID, report.message_id)
        await message.edit(embed=embed)
    except discord.HTTPException:
        pass

@add_hook(EventHooks.report_edit)
async def edit_private_report_messages(report: schemas.ReportWithRelations):
    if not report.messages:
        return
    
    async with session_factory() as db:
        for message_data in report.messages:
            try:
                # Get new message content
                if message_data.community_id == report.token.community_id:
                    embed = await ReportManagementView.get_embed(report)
                    view = ReportManagementView(report)
                else:
                    # Create pending responses
                    community = await get_community_by_id(db, message_data.community_id)
                    responses = await get_pending_responses(db, community, report.players)
                    view = PlayerReviewView(responses=responses)
                    embed = await PlayerReviewView.get_embed(report, responses)

                # Update message
                message = bot.get_partial_message(message_data.channel_id, message_data.message_id)
                await message.edit(embed=embed, view=view)
            except discord.HTTPException:
                pass
            except:
                logging.exception("Unexpected error occurred while attempting to delete %r", message_data)

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
