from datetime import datetime, timezone
import pydantic
import re

from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands, Interaction
from discord.ext import commands

from sqlalchemy import select

from bunker import schemas
from bunker.constants import REPORT_TOKEN_EXPIRE_DELTA
from bunker.crud.communities import get_community_by_id, get_community_by_guild_id, get_admin_by_id
from bunker.crud.reports import delete_report, get_report_by_id, get_reports_for_player
from bunker.crud.responses import get_pending_responses, set_report_response, get_response_stats
from bunker.db import models, session_factory
from bunker.discord.reports import get_report_embed
from bunker.discord.utils import format_url, handle_error, CustomException
from bunker.discord.views.player_review import PlayerReviewView
from bunker.discord.views.report_paginator import ReportPaginator
from bunker.discord.views.submit_report import OpenFormView
from bunker.exceptions import NotFoundError
from bunker.enums import ReportRejectReason
from bunker.hooks import EventHooks
from bunker.urls import get_report_edit_url

if TYPE_CHECKING:
    from bunker.discord.bot import Bot

RE_PRR_CUSTOM_ID = re.compile(r"^prr:(?P<command>\w+):(?P<community_id>\d+):(?P<pr_id>\d+)(?::(?P<reject_reason>[\w_]+))?$")
RE_RM_CUSTOM_ID = re.compile(r"^rm:(?P<command>\w+):(?P<report_id>\d+)$")

class PRRCustomIDPayload(pydantic.BaseModel):
    command: str
    community_id: int
    pr_id: int
    reject_reason: Optional[ReportRejectReason] = None

    @pydantic.field_validator("reject_reason", mode="before")
    @classmethod
    def _validate_reject_reason(cls, value):
        if isinstance(value, str):
            return ReportRejectReason[value]
        return value

class RMCustomIDPayload(pydantic.BaseModel):
    command: str
    report_id: int

class ReportsCog(commands.Cog):
    def __init__(self, bot: 'Bot'):
        self.bot = bot
    
    @commands.Cog.listener()
    async def on_interaction(self, interaction: Interaction):
        # TODO: Replace with dynamic items whenever d.py v2.4 releases
        if interaction.type != discord.InteractionType.component:
            return
        
        custom_id: str = interaction.data['custom_id']
        try:
            if custom_id.startswith("prr:"):
                await self.handle_review_interaction(interaction, custom_id)
            elif custom_id.startswith("rm:"):
                await self.handle_management_interaction(interaction, custom_id)
        except Exception as e:
            await handle_error(interaction, e)

    async def handle_review_interaction(self, interaction: Interaction, custom_id: str):
        match = RE_PRR_CUSTOM_ID.match(custom_id)
        if not match:
            return
            
        data = PRRCustomIDPayload(**match.groupdict())
        match data.command:
            case "refresh":
                # In this case, pr_id is actually the report ID, not the player report ID
                await self.refresh_report_view(interaction, data.community_id, data.pr_id)

            case "ban":
                prr = schemas.ResponseCreateParams(
                    **data.model_dump(exclude={"command"}),
                    banned=True,
                )
                await self.set_response(interaction, prr)

            case "unban":
                prr = schemas.ResponseCreateParams(
                    **data.model_dump(exclude={"command"}),
                    banned=False,
                )
                await self.set_response(interaction, prr)

            case _:
                prr = schemas.ResponseCreateParams(
                    **data.model_dump(exclude={"command"}),
                    banned=False,
                )
                await self.set_response(interaction, prr)

    
    async def handle_management_interaction(self, interaction: Interaction, custom_id: str):
        match = RE_RM_CUSTOM_ID.match(custom_id)
        if not match:
            return
        
        data = RMCustomIDPayload(**match.groupdict())

        async with session_factory.begin() as db:
            match data.command:
                case "del":
                    # TODO: Add confirmation
                    # TODO? Only allow admins to delete
                    await delete_report(db, data.report_id, by=interaction.user)
                    await interaction.message.delete()

                case "edit":
                    db_report = await get_report_by_id(db, data.report_id, load_token=True)
                    if not db_report:
                        raise NotFoundError("This report no longer exists")
                    
                    # Generate new token and update expiration date
                    db_report.token.value = db_report.token.generate_value()
                    db_report.token.expires_at = datetime.now(tz=timezone.utc) + REPORT_TOKEN_EXPIRE_DELTA
                    # Send URL to user
                    url = get_report_edit_url(schemas.ReportWithToken.model_validate(db_report))
                    await interaction.response.send_message(
                        content="## " + format_url("Open Google Form", url),
                        ephemeral=True
                    )

                case _:
                    raise ValueError("Unknown command %s" % data.command)


    async def set_response(self, interaction: Interaction, prr: schemas.ResponseCreateParams):
        async with session_factory.begin() as db:
            db_prr = await set_report_response(db, prr)

            players: list[models.PlayerReport] = await db_prr.player_report.report.awaitable_attrs.players
            responses = {
                player.id: schemas.PendingResponse(
                    pr_id=player.id,
                    player_report=player,
                    community_id=db_prr.community_id,
                    community=db_prr.community,
                ) for player in players
            }
            responses[prr.pr_id].banned = prr.banned
            responses[prr.pr_id].reject_reason = prr.reject_reason

            if len(players) > 1 or players[0].id != prr.pr_id:
                # Load state of other reported players if needed
                stmt = select(
                    models.PlayerReportResponse.pr_id,
                    models.PlayerReportResponse.reject_reason,
                    models.PlayerReportResponse.banned
                ).join(
                    models.PlayerReport
                ).where(
                    models.PlayerReportResponse.community_id == prr.community_id,
                    models.PlayerReport.id.in_(
                        [player.id for player in players if player.id != prr.pr_id]
                    )
                )
                result = await db.execute(stmt)
                for row in result:
                    responses[row.pr_id].banned = row.banned
                    responses[row.pr_id].reject_reason = row.reject_reason

            report = db_prr.player_report.report
            await report.awaitable_attrs.token

            stats: dict[int, schemas.ResponseStats] = {}
            for player in report.players:
                stats[player.id] = await get_response_stats(db, player)

        responses = list(responses.values())
        view = PlayerReviewView(responses=responses)
        embed = await PlayerReviewView.get_embed(report, responses, stats=stats)
        await interaction.response.edit_message(embed=embed, view=view)
    
    async def refresh_report_view(self, interaction: Interaction, community_id: int, report_id: int):
        async with session_factory() as db:
            report = await get_report_by_id(db, report_id, load_token=True)
            community = await get_community_by_id(db, community_id)

            stats: dict[int, schemas.ResponseStats] = {}
            for player in report.players:
                stats[player.id] = await get_response_stats(db, player)

            responses = await get_pending_responses(db, community, report.players)
        view = PlayerReviewView(responses=responses)
        embed = await PlayerReviewView.get_embed(report, responses, stats=stats)
        await interaction.response.edit_message(embed=embed, view=view)


    @app_commands.command(name="reports", description="See all Bunker reports made against a player")
    async def get_reports(self, interaction: Interaction, player_id: str):
        async with session_factory() as db:
            admin = await get_admin_by_id(db, discord_id=interaction.user.id)
            if admin and admin.community:
                community = admin.community
            else:
                community = await get_community_by_guild_id(db, guild_id=interaction.guild_id)

            if not community:
                raise CustomException(
                    "Access denied!",
                    "Only admins of verified servers can use this command."
                )

            reports = await get_reports_for_player(db, player_id=player_id, load_token=True)
            if not reports:
                await interaction.response.send_message(
                    embed=discord.Embed(color=discord.Color.dark_theme()) \
                        .set_author(name="There are no reports made against this player!"),
                    ephemeral=True
                )
                return

            view = ReportPaginator(community, reports)
            await view.send(interaction)

async def setup(bot: 'Bot'):
    await bot.add_cog(ReportsCog(bot))
