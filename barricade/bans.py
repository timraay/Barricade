import asyncio
from functools import partial
from typing import Coroutine, Sequence

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
        callable: Coroutine,
        player_id: str,
        integration: schemas.IntegrationConfig,
        community: schemas.CommunityRef,
        embed: Embed,
        excs: Sequence[type[Exception]] | type[Exception] = None,
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
        bans = await get_player_bans_for_community(db, response.player_report.player_id, community.id)
    
    if len(community.integrations) <= len(bans):
        # Already banned by every integration
        return

    banned_by = set(ban.integration_id for ban in bans)
    report = response.player_report.report
    reasons = report.reasons_bitflag.to_list(report.reasons_custom)
    manager = IntegrationManager()
    
    embed = get_error_embed(
        "Integration failed to ban player!",
        "Retry using the button below."
    )
    
    coros = []
    for config in community.integrations:
        if config.id in banned_by:
            continue

        integration = manager.get_by_config(schemas.IntegrationConfig.model_validate(config))
        coro = forward_errors(
            partial(integration.ban_player, response),
            player_id=response.player_report.player,
            integration=integration.config,
            community=response.community,
            embed=embed,
        )
        coros.append(coro)

    await asyncio.gather(*coros)
        
@add_hook(EventHooks.player_unban)
async def on_player_unban(response: schemas.Response):
    async with session_factory() as db:
        bans = await get_player_bans_without_responses(db, [response.player_report.player_id], community_id=response.community_id)

    if not bans:
        # Either no integrations have banned the players or the players are
        # still banned through another report
        return

    manager = IntegrationManager()
    embed = get_error_embed(
        "Integration failed to unban player!",
        "Either manually delete the ban or retry using the button below."
    )
        
    coros = []
    for ban in bans:
        integration = manager.get_by_config(schemas.IntegrationConfig.model_validate(ban.integration))
        player_id = response.player_report.player_id
        coro = forward_errors(
            partial(integration.unban_player, player_id),
            player_id=player_id,
            integration=integration.config,
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
        bans = await get_player_bans_without_responses(db, detached_player_ids)
    
        manager = IntegrationManager()
        embed = get_error_embed(
            "Integration failed to unban player after they were removed from a report!",
            "Either manually delete the ban or retry using the button below."
        )

        coros = []
        for ban in bans:
            integration = manager.get_by_config(schemas.IntegrationConfig.model_validate(ban.integration))
            community = await ban.integration.awaitable_attrs.community
            coro = forward_errors(
                partial(integration.unban_player, ban.player_id),
                player_id=ban.player_id,
                integration=integration.config,
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
            integration = manager.get_by_config(schemas.IntegrationConfig.model_validate(ban.integration))
            community = await ban.integration.awaitable_attrs.community
            coro = forward_errors(
                partial(integration.unban_player, ban.player_id),
                player_id=ban.player_id,
                integration=integration.config,
                community=community,
                embed=embed,
            )
            coros.append(coro)
    await asyncio.gather(*coros)
