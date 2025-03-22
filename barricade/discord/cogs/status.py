from discord import CustomActivity
from discord.ext import commands, tasks
from sqlalchemy import func, select

from barricade.db import models, session_factory
from barricade.discord.bot import Bot
from barricade.enums import Emojis

class StatusCog(commands.Cog):
    def __init__(self, bot: Bot):
        self.bot = bot
        self.update_bot_status.start()

    @tasks.loop(minutes=10)
    async def update_bot_status(self):
        async with session_factory() as db:
            num_players_reported = await db.scalar(select(func.count()).select_from(models.PlayerReport))
            num_bans_issued = await db.scalar(select(func.count()).select_from(models.PlayerReportResponse).where(models.PlayerReportResponse.banned == True))

        await self.bot.change_presence(activity=CustomActivity(
            name=f"{num_players_reported} players reported | {num_bans_issued} bans issued",
            emoji=Emojis.BANNED
        ))

    @update_bot_status.before_loop
    async def before_update_bot_status(self):
        await self.bot.wait_until_ready()


async def setup(bot: Bot):
    await bot.add_cog(StatusCog(bot))