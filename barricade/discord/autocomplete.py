from discord import Interaction
from discord.app_commands import Choice
from sqlalchemy import select, func

from barricade.db import models, session_factory
from barricade.discord.utils import async_ttl_cache

@async_ttl_cache(size=100, seconds=60)
async def _get_ttl_communities(name: str):
    async with session_factory() as db:
        stmt = select(models.Community).where(
            func.concat(
                models.Community.tag,
                " ",
                models.Community.name
            ).ilike(
                "%" + name.replace(".", "\\.").replace("%", "\\.") + "%"
            )
        ).limit(15)
        result = await db.scalars(stmt)
        return result.all()

async def atcp_community(interaction: Interaction, current: str):
    communities = await _get_ttl_communities(current.lower())
    choices = [
        Choice(name=community.tag + " " + community.name, value=community.id)
        for community in communities
    ]
    return choices
