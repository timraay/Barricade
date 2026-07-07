from collections.abc import Callable
from typing import TypeAlias

import discord
from discord.utils import escape_markdown as esc_md

from barricade import schemas
from barricade.discord.communities import get_admin_name
from barricade.discord.utils import LayoutView, format_url
from barricade.enums import (
    Emojis,
    Game,
    Platform,
    PlayerPlatform,
    ReportReasonFlag,
)
from barricade.utils import PlayerIDType, game_switch, get_player_id_type

HLL_GAME_PILL = "".join(
    [
        Emojis.PILL_HLL_1,
        Emojis.PILL_HLL_2,
        Emojis.PILL_HLL_3,
        Emojis.PILL_HLL_4,
        Emojis.PILL_HLL_5,
    ]
)

HLLV_GAME_PILL = "".join(
    [
        Emojis.PILL_HLLV_1,
        Emojis.PILL_HLLV_2,
        Emojis.PILL_HLLV_3,
        Emojis.PILL_HLLV_4,
        Emojis.PILL_HLLV_5,
    ]
)


def get_game_pill(game: Game) -> str:
    return game_switch(game, HLL_GAME_PILL, HLLV_GAME_PILL)


# TODO: Add more emojis
def get_player_platform_emoji(
    platform: PlayerPlatform | None, server_type: Platform, game: Game
) -> str | None:
    if platform == PlayerPlatform.STEAM:
        return Emojis.STEAM

    elif server_type == Platform.PC:
        return Emojis.EPIC_XBOX

    return None


ReportViewActionRowFactory: TypeAlias = Callable[
    [schemas.PlayerReportRef, schemas.PendingResponse | None],
    discord.ui.ActionRow | None,
]


async def get_plain_report_view(
    report: schemas.ReportWithToken,
    responses: list[schemas.PendingResponse] | None = None,
    stats: dict[int, schemas.ResponseStats] | None = None,
    with_eos_ids: bool = False,
    container_color: discord.Colour | None = None,
    action_row: discord.ui.ActionRow | None = None,
    player_action_row_factory: ReportViewActionRowFactory | None = None,
    refresh_button: discord.ui.Item | None = None,
) -> LayoutView:
    if responses and len(responses) != len(report.players):
        raise ValueError(
            f"Expected {len(report.players)} responses but got {len(responses)}"
        )

    container = discord.ui.Container(accent_color=container_color)

    # Reason(s)
    container.add_item(
        discord.ui.TextDisplay(
            "-# **Reason**\n**"
            + "**\n**".join(
                ReportReasonFlag(report.reasons_bitflag).to_list(
                    report.reasons_custom, with_emoji=True
                )
            )
            + "**"
        )
    )

    container.add_item(
        discord.ui.Separator(visible=False, spacing=discord.SeparatorSpacing.small)
    )

    # Description
    container.add_item(
        discord.ui.TextDisplay(f"-# **Description**\n{report.body.strip()}")
    )

    # Reported player(s)
    for i, player in enumerate(report.players, 1):
        # Add separator between players
        container.add_item(
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large)
        )

        # Determine whether player platform must be Steam
        player_id_type = get_player_id_type(player.player_id)
        is_steam = player_id_type == PlayerIDType.STEAM_64_ID

        # Get response
        response = responses[i - 1] if responses else None  # i starts at 1

        # Build message content
        # Player name
        player_name_prefix = " "
        if response:
            if response.banned is True:
                player_name_prefix = Emojis.HIGHLIGHT_RED
            elif response.banned is False:
                player_name_prefix = Emojis.HIGHLIGHT_GREEN
        content = f"**`{i}.`{player_name_prefix}{esc_md(player.player_name)}**\n"

        # Player ID
        platform_emoji = get_player_platform_emoji(
            player.player.platform, report.server_type, report.game
        )
        if platform_emoji:
            content += f"{platform_emoji} "
        content += f"*`{player.player_id}`*"

        # Player EOS ID
        if with_eos_ids and not is_steam:
            content += f"\n-# {Emojis.EASY_ANTI_CHEAT}"
            content += (
                f"*`{player.player.hll_eos_id}`*"
                if player.player.hll_eos_id
                else "No EOS ID known"
            )

        # Report acceptance rate
        if stats and (stat := stats.get(player.id)):
            num_responses = stat.num_banned + stat.num_rejected
            if num_responses:
                rate = stat.num_banned / num_responses
                if rate >= 0.9:
                    rate_emoji = Emojis.TICK_YES
                elif rate >= 0.7:
                    rate_emoji = Emojis.TICK_MAYBE
                elif rate >= 0.5 or num_responses <= 5:
                    rate_emoji = Emojis.TICK_NO
                else:
                    rate_emoji = "💀"

                content += f"\n{rate_emoji} Banned by **{rate:.0%}** ({stat.num_banned}/{num_responses})"

                reject_reasons = [
                    (reject_reason.value, amount)
                    for reject_reason, amount in stat.reject_reasons.items()
                ]
                reject_reasons.append(
                    ("Unbanned", stat.num_rejected - sum(stat.reject_reasons.values()))
                )

                for reject_reason, amount in sorted(
                    reject_reasons, key=lambda x: x[1], reverse=True
                ):
                    if amount > 0:
                        content += f"\n-# {Emojis.ARROW_DOWN_RIGHT}{Emojis.TICK_NO} {amount}x **{reject_reason}**"

        # Links
        links = []
        if is_steam:
            links.append(
                format_url(
                    "Steam",
                    f"https://steamcommunity.com/profiles/{player.player_id}",
                )
            )

        bm_rcon_url = player.player.bm_rcon_url
        if bm_rcon_url:
            links.append(format_url("Battlemetrics", bm_rcon_url))

        links.append(
            format_url(
                "HLLRecords", f"https://hllrecords.com/profiles/{player.player_id}"
            )
        )

        content += "\nView on " + " | ".join(links)
        # TODO: Add Steam profile as thumbnail image where possible
        container.add_item(discord.ui.TextDisplay(content))

        # Buttons
        if player_action_row_factory:
            player_action_row = player_action_row_factory(player, response)
            if player_action_row:
                container.add_item(player_action_row)

        # Responded by
        if response and response.responded_by:
            # TODO: Use user mentions? Check whether components v2 has fixed the cache bug
            content = f"\n-# Reviewed by **{esc_md(response.responded_by)}**"
            if response.responded_at:
                content += f" on {discord.utils.format_dt(response.responded_at, 'f')}"
            content += f" {Emojis.BANNED if response.banned else Emojis.UNBANNED}"
            container.add_item(discord.ui.TextDisplay(content))

    if action_row:
        container.add_item(
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large)
        )
        container.add_item(action_row)

    view = LayoutView(timeout=None)
    view.add_item(container)

    # Created/edited at
    admin_name = await get_admin_name(report.token.admin)
    community_url = report.token.community.contact_url
    if not community_url.startswith(("http://", "https://")):
        community_url = "https://" + community_url

    content = (
        f"-# Reported by {admin_name}"
        f" of [{report.token.community.name}]({community_url})"
        f" on {discord.utils.format_dt(report.created_at, 'f')}"
    )

    if report.edited_by or report.edited_at:
        content += "\nLast edited"
        if report.edited_by:
            content += f" by {report.edited_by}"
        if report.edited_at:
            content += f" on {discord.utils.format_dt(report.edited_at, 'f')}"

    if refresh_button:
        view.add_item(
            discord.ui.Section(
                discord.ui.TextDisplay(content),
                accessory=refresh_button,
            )
        )
    else:
        view.add_item(discord.ui.TextDisplay(content))

    return view
