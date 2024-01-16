import asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from bunker import schemas
from bunker.db import models
from bunker.enums import IntegrationType
from bunker.hooks import EventHooks, add_hook
from bunker.integrations import BattlemetricsIntegration, CRCONIntegration

async def set_report_response(db: AsyncSession, prr: schemas.ResponseCreateParams):
    db_prr = await db.get(models.PlayerReportResponse, (prr.pr_id, prr.community_id))

    if not db_prr:
        db_prr = models.PlayerReportResponse(**prr.model_dump())
        db.add(db_prr)
        await db.commit()
        await db.refresh(db_prr)

    else:
        db_prr.banned = prr.banned
        db_prr.reject_reason = prr.reject_reason
        await db.commit()

    prr = schemas.Response.model_validate(db_prr)
    if prr.banned:
        EventHooks.invoke_player_ban(prr)
    else:
        EventHooks.invoke_player_unban(prr)

    return db_prr


def get_integration(config: schemas.IntegrationConfig):
    if config.integration_type == IntegrationType.BATTLEMETRICS:
       return BattlemetricsIntegration(config)
    elif config.integration_type == IntegrationType.COMMUNITY_RCON:
       return CRCONIntegration(config)
    else:
        raise TypeError("Missing implementation for integration type %r" % config.integration_type)


@add_hook(EventHooks.player_ban)
async def on_player_ban(response: schemas.Response):
    coros = []
    for config in response.community.integrations:
        integration = get_integration(config)
        coros.append(integration.ban_player())
    await asyncio.gather(*coros)
        
@add_hook(EventHooks.player_unban)
async def on_player_ban(response: schemas.Response):
    coros = []
    for config in response.community.integrations:
        integration = get_integration(config)
        coros.append(integration.ban_player())
    await asyncio.gather(*coros)
