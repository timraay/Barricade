import logging

import discord

from barricade import schemas
from barricade.constants import (
    DISCORD_ATTACHMENTS_CHANNEL_ID,
    DISCORD_HLL_REPORTS_CHANNEL_ID,
    DISCORD_HLLV_REPORTS_CHANNEL_ID,
    T17_SUPPORT_HLL_CHANNEL_ID,
    T17_SUPPORT_HLLV_CHANNEL_ID,
)
from barricade.discord.bot import bot
from barricade.discord.utils import format_url
from barricade.discord.views.report import get_player_platform_emoji
from barricade.enums import (
    Game,
    PlayerAlertType,
    ReportReasonFlag,
)
from barricade.utils import PlayerIDType, game_switch, get_player_id_type


def get_report_channel(game: Game) -> discord.TextChannel:
    channel_id = game_switch(
        game, DISCORD_HLL_REPORTS_CHANNEL_ID, DISCORD_HLLV_REPORTS_CHANNEL_ID
    )

    channel = bot.primary_guild.get_channel(channel_id)
    if not channel:
        raise RuntimeError(f"{game.name} report channel could not be found") from None
    elif not isinstance(channel, discord.TextChannel):
        raise RuntimeError(
            f"{game.name} report channel is not a text channel"
        ) from None
    return channel


def get_t17_support_forward_channel(game: Game) -> discord.TextChannel | None:
    channel_id = game_switch(
        game, T17_SUPPORT_HLL_CHANNEL_ID, T17_SUPPORT_HLLV_CHANNEL_ID
    )

    channel = bot.primary_guild.get_channel(channel_id)
    if not channel:
        logging.warning("T17 Support forward channel could not be found")
    elif not isinstance(channel, discord.TextChannel):
        logging.error("T17 Support forward channel is not a text channel")
        channel = None
    return channel


def get_attachments_channel() -> discord.TextChannel | None:
    channel = bot.primary_guild.get_channel(DISCORD_ATTACHMENTS_CHANNEL_ID)
    if not channel:
        logging.warning("Attachments channel could not be found")
    elif not isinstance(channel, discord.TextChannel):
        logging.error("Attachments channel is not a text channel")
        channel = None
    return channel


def get_alert_embed(
    reports_urls: list[tuple[schemas.Report, str]],
    player: schemas.PlayerReportRef,
    alert_type: PlayerAlertType,
):
    player_id_type = get_player_id_type(player.player_id)

    title = f"{player.player_name}\n{get_player_platform_emoji(player.player.platform)} *`{player.player_id}`*"
    description = []

    if player_id_type == PlayerIDType.STEAM_64_ID:
        description.append(
            format_url(
                "View on Steam",
                f"https://steamcommunity.com/profiles/{player.player_id}",
            )
        )

    bm_rcon_url = player.player.bm_rcon_url
    if bm_rcon_url:
        description.append(format_url("View on Battlemetrics", bm_rcon_url))

    if description:
        description.append("")

    if alert_type == PlayerAlertType.UNREVIEWED:
        if len(reports_urls) == 1:
            description.append(
                "There is a report against this player that has not yet been reviewed."
            )
        else:
            description.append(
                f"There are {len(reports_urls)} reports against this player that have not yet been reviewed."
            )

    embed = discord.Embed(
        title=title, description="\n".join(description), colour=discord.Colour.red()
    )

    for report, message_url in reports_urls:
        embed.add_field(
            name="\n".join(
                ReportReasonFlag(report.reasons_bitflag).to_list(
                    report.reasons_custom, with_emoji=True
                )
            ),
            value=f"{message_url}\n{discord.utils.format_dt(report.created_at, 'R')}",
        )

    return embed
