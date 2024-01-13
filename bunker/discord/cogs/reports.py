import pydantic
import re

from typing import TYPE_CHECKING, Optional

import discord
from discord import Interaction
from discord.ext import commands

from sqlalchemy import select

from bunker import schemas
from bunker.db import models, session_factory
from bunker.communities import get_community_by_id
from bunker.discord.utils import handle_error
from bunker.discord.views.player_review import PlayerReviewView
from bunker.enums import ReportRejectReason
from bunker.reports import get_report_by_id, set_report_response

if TYPE_CHECKING:
    from bunker.discord.bot import Bot

RE_PRR_CUSTOM_ID = re.compile(r"^prr:(?P<command>\w+):(?P<community_id>\d+):(?P<pr_id>\d+)(?::(?P<reject_reason>[\w_]+))?$")

class PRRCustomIDPayload(pydantic.BaseModel):
    command: str
    community_id: int
    pr_id: int
    reject_reason: Optional[ReportRejectReason] = None

    @pydantic.field_validator("reject_reason")
    @classmethod
    def _validate_reject_reason(cls, value):
        if isinstance(value, str):
            return ReportRejectReason[value]
        return value

class ReportsCog(commands.Cog):
    def __init__(self, bot: 'Bot'):
        self.bot = bot
    
    @commands.Cog.listener()
    async def on_interaction(self, interaction: Interaction):
        # TODO: Replace with dynamic items whenever d.py v2.4 releases
        if interaction.type != discord.InteractionType.component:
            return
        
        custom_id: str = interaction.data['custom_id']
        if not custom_id.startswith("prr:"):
            return
        print("custom_id:", custom_id)
        
        match = RE_PRR_CUSTOM_ID.match(custom_id)
        if not match:
            return
            
        try:
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

        except Exception as e:
            await handle_error(interaction, e)

    async def set_response(self, interaction: Interaction, prr: schemas.ResponseCreateParams):
        async with session_factory() as db:
            db_prr = await set_report_response(db, prr)

            players: list[models.PlayerReport] = await db_prr.player_report.report.awaitable_attrs.players
            responses = {
                player.id: schemas.PendingResponse(
                    player_report=player,
                    community=db_prr.community
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

        view = PlayerReviewView(responses=list(responses.values()))
        await interaction.response.edit_message(view=view)
    
    async def refresh_report_view(self, interaction: Interaction, community_id: int, report_id: int):
        async with session_factory() as db:
            report = await get_report_by_id(db, report_id)
            community = await get_community_by_id(db, report_id)
            
            stmt = select(
                models.PlayerReportResponse.pr_id,
                models.PlayerReportResponse.reject_reason,
                models.PlayerReportResponse.banned
            ).join(
                models.PlayerReport
            ).where(
                models.PlayerReportResponse.community_id == community_id,
                models.PlayerReport.id.in_(
                    [player.id for player in report.players]
                )
            )
            result = await db.execute(stmt)
            responses = [
                schemas.PendingResponse(
                    banned=row.banned,
                    reject_reason=row.reject_reason,
                    player_report=next(player for player in report.players if player.id == row.pr_id),
                    community=community
                )
                for row in result
            ]

        view = PlayerReviewView(responses=responses)
        await interaction.response.edit_message(view=view)

async def setup(bot: 'Bot'):
    await bot.add_cog(ReportsCog(bot))
