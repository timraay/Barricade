import asyncio
from functools import partial
import logging
from typing import Callable, Coroutine, Sequence
from sqlalchemy.ext.asyncio import AsyncSession

from discord import Embed

from barricade import schemas
from barricade.crud.bans import get_player_bans_for_community, get_player_bans_without_responses
from barricade.crud.communities import get_community_by_id
from barricade.crud.watchlists import get_watchlist_by_player_and_community
from barricade.db import models, session_factory
from barricade.discord.communities import get_forward_channel
from barricade.discord.utils import get_error_embed
from barricade.discord.views.retry_error import RetryErrorView
from barricade.hooks import EventHooks, add_hook
from barricade.integrations.integration import Integration
from barricade.integrations.manager import IntegrationManager
from barricade.logger import get_logger

async def forward_errors(
        callable: Callable[..., Coroutine],
        player_id: str,
        integration: schemas.IntegrationConfig,
        community: schemas.CommunityRef,
        embed: Embed,
        excs: tuple[type[Exception]] | type[Exception] | None = None,
):
    try:
        await callable()
    except Exception as e:
        get_logger(community.id).exception("Failed to forward request: %s", type(e).__name__)
        if not excs or isinstance(e, excs):
            channel = get_forward_channel(community)
            if not channel:
                return
            
            embed.add_field(
                name="Player ID",
                value=player_id
            ).add_field(
                name="Integration",
                value=f"{integration.integration_type.value} (#{integration.id})"
            ).add_field(
                name="Details",
                value=f"`{str(e)}`",
                inline=False
            )

            view = RetryErrorView(callable)
            await channel.send(view=view, embed=embed)


@add_hook(EventHooks.player_ban)
async def on_player_ban(response: schemas.ResponseWithToken):
    async with session_factory() as db:
        community = await get_community_by_id(db, response.community_id)
        assert community is not None

        bans = await get_player_bans_for_community(db, response.player_report.player_id, community.id)
    
    if len(community.integrations) <= len(bans):
        # Already banned by every integration
        return

    banned_by = set(ban.integration_id for ban in bans)
    # report = response.player_report.report
    # reasons = report.reasons_bitflag.to_list(report.reasons_custom)
    manager = IntegrationManager()
    
    embed = get_error_embed(
        "Integration failed to ban player!",
        "Retry using the button below."
    )
    
    coros = []
    for db_integration in community.integrations:
        if db_integration.id in banned_by:
            continue

        config = schemas.IntegrationConfig.model_validate(db_integration)
        integration = manager.get_by_config(config)
        if not integration:
            logger = get_logger(community.id)
            logger.error("Integration with config %r should be registered by manager but was not" % config)
            continue

        if not integration.config.enabled:
            continue

        coro = forward_errors(
            partial(integration.ban_player, response),
            player_id=response.player_report.player_id,
            integration=config,
            community=response.community,
            embed=embed,
        )
        coros.append(coro)

    await asyncio.gather(*coros)
        
@add_hook(EventHooks.player_unban)
async def on_player_unban(response: schemas.Response):
    async with session_factory() as db:
        db_bans = await get_player_bans_without_responses(
            db,
            player_ids=[response.player_report.player_id],
            community_id=response.community_id,
        )

    if not db_bans:
        # Either no integrations have banned the players or the players are
        # still banned through another report
        return
        
    coros = []
    for db_ban in db_bans:
        integration = get_integration_from_ban(db_ban)
        if not integration:
            continue

        player_id = response.player_report.player_id

        coro = revoke_ban(
            integration,
            response.community,
            player_id,
        )
        coros.append(coro)

    await asyncio.gather(*coros)

@add_hook(EventHooks.report_edit)
async def unban_players_detached_from_report(report: schemas.ReportWithRelations, old_report: schemas.ReportWithToken):
    old_player_ids = {player.player_id for player in old_report.players}
    new_player_ids = {player.player_id for player in report.players}
    detached_player_ids = list(old_player_ids.difference(new_player_ids))

    if not detached_player_ids:
        return
    
    async with session_factory() as db:
        amount = await revoke_dangling_bans(db, player_ids=detached_player_ids)
        logging.info("Revoked %d dangling bans for players %s on report edit", amount, detached_player_ids)

@add_hook(EventHooks.report_delete)
async def unban_player_on_report_delete(report: schemas.ReportWithRelations):
    player_ids = [player.player_id for player in report.players]
    async with session_factory() as db:
        amount = await revoke_dangling_bans(db, player_ids=player_ids)
        logging.info("Revoked %d dangling bans for players %s on report delete", amount, player_ids)

@add_hook(EventHooks.player_ban)
async def remove_banned_players_from_watchlist(response: schemas.ResponseWithToken):
    async with session_factory.begin() as db:
        db_watchlist = await get_watchlist_by_player_and_community(
            db,
            player_id=response.player_report.player_id,
            community_id=response.community_id,
        )
        if db_watchlist:
            await db.delete(db_watchlist)

async def revoke_dangling_bans(
    db: AsyncSession,
    player_ids: Sequence[str] | None = None,
    community_id: int | None = None,
) -> int:
    db_bans = await get_player_bans_without_responses(
        db,
        player_ids=player_ids,
        community_id=community_id,
    )

    coros = []
    for db_ban in db_bans:
        integration = get_integration_from_ban(db_ban)
        if not integration:
            continue

        db_community = await db_ban.integration.awaitable_attrs.community
        community = schemas.CommunityRef.model_validate(db_community)

        coro = revoke_ban(
            integration,
            community,
            db_ban.player_id,
        )
        coros.append(coro)

    await asyncio.gather(*coros)
    return len(db_bans)

def get_integration_from_ban(db_ban: models.PlayerBan) -> Integration | None:
    db_integration = db_ban.integration
    config = schemas.IntegrationConfig.model_validate(db_integration)
    integration = IntegrationManager().get_by_config(config)
    if not integration:
        logger = get_logger(config.community_id)
        logger.error("Integration with config %r should be registered by manager but was not" % config)
    return integration

async def revoke_ban(
    integration: Integration,
    community: schemas.CommunityRef,
    player_id: str,
):
    if not isinstance(integration.config, schemas.IntegrationConfig):
        raise ValueError("Integration needs to be saved first")

    embed = get_error_embed(
        "Integration failed to unban player!",
        "Either manually delete the ban or retry using the button below."
    )

    await forward_errors(
        partial(integration.unban_player, player_id),
        player_id=player_id,
        integration=integration.config,
        community=community,
        embed=embed,
    )
