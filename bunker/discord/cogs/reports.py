import re

from typing import TYPE_CHECKING

import discord
from discord import Interaction
from discord.ext import commands
from discord.utils import escape_markdown as esc_md

from sqlalchemy import select

from bunker import schemas
from bunker.db import models, session_factory
from bunker.discord.utils import handle_error
from bunker.discord.views.player_review import PlayerReviewView

if TYPE_CHECKING:
    from bunker.discord.bot import Bot

RE_PRR_CUSTOM_ID = re.compile(r"^prr:(\d+):(\d+):([01])$")

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
            prr = schemas.ResponseCreateParams(
                community_id = int(match.group(1)),
                pr_id = int(match.group(2)),
                banned = bool(int(match.group(3)))
            )
            async with session_factory() as db:
                db_prr = models.PlayerReportResponse(**prr.model_dump())
                db.add(db_prr)
                await db.commit()
                await db.refresh(db_prr)

                players: list[models.PlayerReport] = await db_prr.player_report.report.awaitable_attrs.players
                responses = {
                    player.id: schemas.PendingResponse(
                        player_report=player,
                        community=db_prr.community
                    ) for player in players
                }
                responses[prr.pr_id].banned = prr.banned

                if len(players) > 1 or players[0].id != prr.pr_id:
                    # Load state of other reported players if needed
                    stmt = select(
                        models.PlayerReportResponse.pr_id,
                        models.PlayerReportResponse.banned
                    ).where(
                        models.PlayerReportResponse.community_id == prr.community_id,
                        models.PlayerReport.id.in_(
                            [player.id for player in players if player.id != prr.pr_id]
                        )
                    )
                    result = await db.scalars(stmt)
                    for row in result.all():
                        responses[row.pr_id].banned = row.banned

            view = PlayerReviewView(responses=list(responses.values()))
            await interaction.response.edit_message(view=view)

        except Exception as e:
            await handle_error(interaction, e)


async def setup(bot: 'Bot'):
    await bot.add_cog(ReportsCog(bot))
