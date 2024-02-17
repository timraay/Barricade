import asyncio

from bunker import schemas
from bunker.crud.bans import get_player_bans_for_community
from bunker.crud.communities import get_community_by_id
from bunker.db import models, session_factory
from bunker.enums import IntegrationType
from bunker.hooks import EventHooks, add_hook
from bunker.integrations import BattlemetricsIntegration, CRCONIntegration

def get_integration(config: schemas.BasicIntegrationConfig):
    if config.integration_type == IntegrationType.BATTLEMETRICS:
       return BattlemetricsIntegration(config)
    elif config.integration_type == IntegrationType.COMMUNITY_RCON:
       return CRCONIntegration(config)
    else:
        raise TypeError("Missing implementation for integration type %r" % config.integration_type)


@add_hook(EventHooks.player_ban)
async def on_player_ban(response: schemas.Response):
    async with session_factory() as db:
        community = await get_community_by_id(db, response.community_id)
        bans = await get_player_bans_for_community(db, response.player_report.player_id, community.id)
    
    if len(community.integrations) <= len(bans):
        # Already banned by every integration
        return

    banned_by = set(ban.integration_id for ban in bans)
    coros = []
    for config in community.integrations:
        if config.id in banned_by:
            continue

        integration = get_integration(config)
        coros.append(integration.ban_player(response))

    await asyncio.gather(*coros)
        
@add_hook(EventHooks.player_unban)
async def on_player_unban(response: schemas.Response):
    async with session_factory() as db:
        bans = await get_player_bans_for_community(db, response.player_report.player_id, response.community_id)
        
    coros = []
    for ban in bans:
        integration = get_integration(ban.integration)
        coros.append(integration.unban_player(response))
    await asyncio.gather(*coros)
