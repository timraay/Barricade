import asyncio
from functools import partial
import logging
from typing import Callable, Coroutine, Sequence

from discord import Embed

from barricade import schemas
from barricade.crud.bans import get_player_bans_for_community, get_player_bans_without_responses
from barricade.crud.communities import get_community_by_id
from barricade.db import session_factory
from barricade.discord import bot
from barricade.discord.communities import get_forward_channel
from barricade.discord.utils import get_error_embed
from barricade.discord.views.retry_error import RetryErrorView
from barricade.hooks import EventHooks, add_hook
from barricade.integrations.manager import IntegrationManager

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
            )

            view = RetryErrorView(callable)
            await channel.send(view=view, embed=embed)
        raise


@add_hook(EventHooks.player_ban)
async def on_player_ban(response: schemas.Response):
    async with session_factory() as db:
        community = await get_community_by_id(db, response.community_id)
        assert community is not None

        bans = await get_player_bans_for_community(db, response.player_report.player_id, community.id)
    
    if len(community.integrations) <= len(bans):
        # Already banned by every integration
        return

    banned_by = set(ban.integration_id for ban in bans)
    report = response.player_report.report
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
            logging.error("Integration with config %r should be registered by manager but was not" % config)
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
        db_bans = await get_player_bans_without_responses(db, [response.player_report.player_id], community_id=response.community_id)

    if not db_bans:
        # Either no integrations have banned the players or the players are
        # still banned through another report
        return

    manager = IntegrationManager()
    embed = get_error_embed(
        "Integration failed to unban player!",
        "Either manually delete the ban or retry using the button below."
    )
        
    coros = []
    for db_ban in db_bans:
        db_integration = db_ban.integration
        config = schemas.IntegrationConfig.model_validate(db_integration)
        integration = manager.get_by_config(config)
        if not integration:
            logging.error("Integration with config %r should be registered by manager but was not" % config)
            continue

        player_id = response.player_report.player_id
        coro = forward_errors(
            partial(integration.unban_player, player_id),
            player_id=player_id,
            integration=config,
            community=response.community,
            embed=embed,
        )
        coros.append(coro)
    await asyncio.gather(*coros)

@add_hook(EventHooks.report_edit)
async def unban_players_detached_from_report(report: schemas.ReportWithRelations, old_report: schemas.ReportWithToken):
    old_player_ids = {player.player_id for player in old_report.players}
    new_player_ids = {player.player_id for player in report.players}
    detached_player_ids = old_player_ids.difference(new_player_ids)

    if not detached_player_ids:
        return
    
    async with session_factory() as db:
        db_bans = await get_player_bans_without_responses(db, list(detached_player_ids))
    
        manager = IntegrationManager()
        embed = get_error_embed(
            "Integration failed to unban player after they were removed from a report!",
            "Either manually delete the ban or retry using the button below."
        )

        coros = []
        for ban in db_bans:
            config = schemas.IntegrationConfig.model_validate(ban.integration)
            integration = manager.get_by_config(config)
            if not integration:
                logging.error("Integration with config %r should be registered by manager but was not" % config)
                continue

            db_community = await ban.integration.awaitable_attrs.community
            community = schemas.CommunityRef.model_validate(db_community)

            coro = forward_errors(
                partial(integration.unban_player, ban.player_id),
                player_id=ban.player_id,
                integration=config,
                community=community,
                embed=embed,
            )
            coros.append(coro)
    await asyncio.gather(*coros)

@add_hook(EventHooks.report_delete)
async def unban_player_on_report_delete(report: schemas.ReportWithRelations):
    player_ids = [player.player_id for player in report.players]

    async with session_factory() as db:
        bans = await get_player_bans_without_responses(db, player_ids)
    
        manager = IntegrationManager()
        embed = get_error_embed(
            "Integration failed to unban player after a report was deleted!",
            "Either manually delete the ban or retry using the button below."
        )

        coros = []
        for ban in bans:
            config = schemas.IntegrationConfig.model_validate(ban.integration)
            integration = manager.get_by_config(config)
            if not integration:
                logging.error("Integration with config %r should be registered by manager but was not" % config)
                continue

            db_community = await ban.integration.awaitable_attrs.community
            community = schemas.CommunityRef.model_validate(db_community)

            coro = forward_errors(
                partial(integration.unban_player, ban.player_id),
                player_id=ban.player_id,
                integration=config,
                community=community,
                embed=embed,
            )
            coros.append(coro)
    await asyncio.gather(*coros)
