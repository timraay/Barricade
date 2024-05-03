import asyncio
import logging
from typing import Coroutine, Sequence

from discord import Embed
import discord

from bunker import schemas
from bunker.constants import DISCORD_REPORTS_CHANNEL_ID
from bunker.crud.bans import get_player_bans_for_community, get_player_bans_without_responses
from bunker.crud.communities import get_community_by_id
from bunker.db import session_factory
from bunker.discord import bot
from bunker.discord.communities import get_forward_channel
from bunker.discord.utils import get_error_embed
from bunker.discord.views.retry_error import RetryErrorView
from bunker.hooks import EventHooks, add_hook
from bunker.integrations.manager import IntegrationManager

async def forward_errors(
        callable: Coroutine,
        *args,
        player_id: str,
        integration: schemas.IntegrationConfig,
        community: schemas.CommunityRef,
        embed: Embed,
        excs: Sequence[type[Exception]] | type[Exception] = None,
        **kwargs
):
    try:
        await callable(*args, **kwargs)
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

            view = RetryErrorView(callable, *args, **kwargs)
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
            integration.ban_player,
            schemas.IntegrationBanPlayerParams(
                player_id=response.player_report.player_id,
                community=response.community,
                reasons=reasons,
            ),
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
        bans = await get_player_bans_for_community(db, response.player_report.player_id, response.community_id)

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
            integration.unban_player,
            player_id,
            player_id=player_id,
            integration=integration.config,
            community=response.community,
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
                integration.unban_player,
                ban.player_id,
                ban=ban,
                community=community,
                embed=embed,
            )
            coros.append(coro)
    await asyncio.gather(*coros)
