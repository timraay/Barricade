import discord
import logging
from sqlalchemy import select

from bunker import schemas
from bunker.db import models, session_factory
from bunker.discord import bot
from bunker.discord.communities import get_forward_channel
from bunker.discord.views.player_review import PlayerReviewView
from bunker.discord.views.report_management import ReportManagementView
from bunker.hooks import EventHooks, add_hook

@add_hook(EventHooks.report_create)
async def forward_report_to_communities(report: schemas.ReportWithToken):
    async with session_factory() as db:
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
            channel = get_forward_channel(community)
            if not channel:
                return
            
            responses = [schemas.PendingResponse(
                pr_id=player.id,
                community_id=community.id,
                player_report=player,
                community=community
            ) for player in report.players]

            view = PlayerReviewView(responses=responses)
            embed = await PlayerReviewView.get_embed(report, responses)
            await channel.send(embed=embed, view=view)

@add_hook(EventHooks.report_create)
async def forward_report_to_token_owner(report: schemas.ReportWithToken):
    community = report.token.community
    admin = report.token.admin

    embed = await ReportManagementView.get_embed(report)
    view = ReportManagementView(report)

    user = await bot.get_or_fetch_user(admin.discord_id)

    if community.forward_channel_id:
        channel = get_forward_channel(community)
        if channel:
            try:
                await channel.send(
                    content=f"{user.mention} your report was submitted! (ID: #{report.id})",
                    embed=embed,
                    view=view,
                )
            except discord.HTTPException:
                pass
            else:
                return
    
    try:
        await user.send(
            content=user.mention,
            embed=embed,
        )
    except discord.errors.HTTPException:
        logging.error("Could not send report confirmation to %s (ID: %s)", admin.name, admin.discord_id)
