from typing import TYPE_CHECKING

import discord
from discord import app_commands, Interaction
from discord.ext import commands

from barricade.crud.communities import get_community_by_guild_id, get_admin_by_id
from barricade.crud.reports import get_reports_for_player
from barricade.db import session_factory
from barricade.discord.utils import CustomException
from barricade.discord.views.report_paginator import ReportPaginator

if TYPE_CHECKING:
    from barricade.discord.bot import Bot

class ReportsCog(commands.Cog):
    def __init__(self, bot: 'Bot'):
        self.bot = bot

    @app_commands.command(name="reports", description="See all Barricade reports made against a player")
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
