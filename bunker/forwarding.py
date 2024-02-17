from sqlalchemy import select

from bunker import schemas
from bunker.db import models, session_factory
from bunker.discord import bot
from bunker.hooks import EventHooks, add_hook

@add_hook(EventHooks.report_create)
async def forward_report_to_communities(report: schemas.ReportWithToken):
    embed = None
    async with session_factory() as db:
        stmt = select(models.Community).where(
            models.Community.forward_guild_id.is_not(None),
            models.Community.forward_channel_id.is_not(None),
        )
        result = await db.scalars(stmt)
        communities = result.all()

        for community in communities:
            if embed is None:
                embed = await bot.get_report_embed(report)
            await bot.forward_report_to_community(report, community, embed)
