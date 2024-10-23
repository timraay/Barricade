from discord import Interaction
from discord.app_commands import Choice
from sqlalchemy import select, func

from barricade.db import models, session_factory
from barricade.discord.utils import async_ttl_cache
from barricade.enums import IntegrationType
from barricade.integrations.manager import IntegrationManager

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

@async_ttl_cache(size=100, seconds=60)
async def _get_ttl_integrations_by_admin_id(admin_id: int):
    async with session_factory() as db:
        db_admin = await db.get(models.Admin, admin_id)
        if not db_admin or not db_admin.community_id:
            return []
        
        im = IntegrationManager()
        return [
            i for i in im.get_all()
            if i.config.community_id == db_admin.community_id
        ]

async def atcp_community(interaction: Interaction, current: str):
    communities = await _get_ttl_communities(current.lower())
    choices = [
        Choice(name=community.tag + " " + community.name, value=community.id)
        for community in communities
    ]
    return choices

async def atcp_integration_enabled(interaction: Interaction, current: str):
    integrations = await _get_ttl_integrations_by_admin_id(interaction.user.id)
    choices: list[Choice[str]] = []
    for integration in integrations:
        if not integration.config.enabled:
            continue

        if integration.meta.type == IntegrationType.BATTLEMETRICS:
            name = f"{integration.meta.name} (Org ID: {integration.config.organization_id})"
        else:
            name = f"{integration.meta.name} ({integration.config.api_url})"

        integration_id = str(integration.config.id)
        if current.lower() not in name.lower() and current not in integration_id:
            continue
        
        choice = Choice(name=name, value=integration_id)
        choices.append(choice)
    return choices
