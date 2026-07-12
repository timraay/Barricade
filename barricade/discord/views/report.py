import asyncio
from collections.abc import Callable, Sequence
from typing import TypeAlias

import discord
from discord.utils import escape_markdown as esc_md

from barricade import schemas
from barricade.constants import REPORT_MAX_ATTACHMENTS
from barricade.discord.utils import LayoutView, format_url, get_user_id_from_mention
from barricade.enums import (
    Emojis,
    Game,
    PlatformFlag,
    PlayerPlatform,
    ReportReasonFlag,
)
from barricade.steam import get_steam_avatar_url
from barricade.utils import PlayerIDType, game_switch, get_player_id_type, validate_url

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
    return "**`" + game_switch(game, "🌲 Hell Let Loose ", "🌴 HLL: Vietnam ") + "`**"
    return game_switch(game, HLL_GAME_PILL, HLLV_GAME_PILL)


def get_platform_pill(platform: PlatformFlag) -> str:
    if platform == PlatformFlag.PC:
        return "**`🕹️ PC `**"
    elif platform == PlatformFlag.CONSOLE:
        return "**`🕹️ Console `**"
    return "**`🕹️ PC & Console `**"


def get_player_platform_emoji(
    player_platform: PlayerPlatform | None,
    platforms: PlatformFlag | None = None,
) -> str:
    if player_platform:
        return Emojis[player_platform.name]

    if platforms == PlatformFlag.CONSOLE:
        return Emojis.XBOX_PLAYSTATION

    if platforms == PlatformFlag.PC:
        return Emojis.EPIC_XBOX

    return Emojis.EPIC_XBOX_PLAYSTATION


ReportViewActionRowFactory: TypeAlias = Callable[
    [schemas.PlayerReportRef, schemas.PendingResponse | None],
    discord.ui.ActionRow | None,
]


def container_add_reasons(
    container: discord.ui.Container,
    report: schemas._ReportBase,
) -> None:
    # Reason(s)
    reasons = ReportReasonFlag(report.reasons_bitflag).to_list(
        report.reasons_custom, with_emoji=True
    )
    container.add_item(
        discord.ui.TextDisplay(
            f"-# **{'Reason' if len(reasons) == 1 else 'Reasons'}**\n"
            + (("**" + "**\n**".join(reasons) + "**") if reasons else "-# Missing")
        )
    )


def container_add_description(
    container: discord.ui.Container,
    report: schemas._ReportBase,
) -> None:
    # Description
    container.add_item(
        discord.ui.TextDisplay(
            f"-# **Description**\n{report.body.strip() or '-# Missing'}"
        )
    )


def container_add_attachments(
    container: discord.ui.Container,
    report: schemas._ReportBase,
) -> None:
    if not report.attachment_urls:
        return

    container.add_item(discord.ui.Separator(visible=False))
    container.add_item(
        discord.ui.MediaGallery(
            *(
                discord.MediaGalleryItem(url)
                for url in report.attachment_urls[:REPORT_MAX_ATTACHMENTS]
            )
        )
    )


def container_add_player(
    container: discord.ui.Container,
    report: schemas._ReportBase,
    player: schemas.PlayerReportRef | schemas.PlayerReportCreateParams,
    rank: int,
    avatar_url: str | None = None,
    response: schemas.PendingResponse | None = None,
    stats: schemas.ResponseStats | None = None,
    with_eos_ids: bool = False,
) -> None:
    # Add separator between players
    container.add_item(
        discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large)
    )

    # Determine whether player platform must be Steam
    try:
        player_id_type = get_player_id_type(player.player_id)
    except ValueError:
        player_id_type = None
    is_steam = player_id_type == PlayerIDType.STEAM_64_ID

    # Build message content
    # Player name
    player_name_prefix = " "
    if response:
        if response.banned is True:
            player_name_prefix = Emojis.HIGHLIGHT_RED
        elif response.banned is False:
            player_name_prefix = Emojis.HIGHLIGHT_GREEN
    content = f"**`{rank}.`{player_name_prefix}{esc_md(player.player_name)}**\n"

    if isinstance(player, schemas.PlayerReportRef):
        player_platform = player.player.platform
        player_eos_id = game_switch(
            report.game,
            player.player.hll_eos_id,
            player.player.hllv_eos_id,
        )
        bm_rcon_url = player.player.bm_rcon_url
    else:
        player_platform = player.platform
        player_eos_id = game_switch(
            report.game,
            player.hll_eos_id,
            player.hllv_eos_id,
        )
        bm_rcon_url = player.bm_rcon_url

    # Player ID
    platform_emoji = get_player_platform_emoji(
        player_platform,
        report.platforms_bitflag,
    )
    content += f"{platform_emoji} *`{player.player_id}`*"

    # Player EOS ID
    if with_eos_ids and not is_steam:
        content += f"\n-# {Emojis.EASY_ANTI_CHEAT}"
        content += f"*`{player_eos_id}`*" if player_eos_id else "No EOS ID known"

    # Report acceptance rate
    if stats:
        num_responses = stats.num_banned + stats.num_rejected
        if num_responses:
            rate = stats.num_banned / num_responses
            if rate >= 0.9:
                rate_emoji = Emojis.TICK_YES
            elif rate >= 0.7:
                rate_emoji = Emojis.TICK_MAYBE
            elif rate >= 0.5 or num_responses <= 5:
                rate_emoji = Emojis.TICK_NO
            else:
                rate_emoji = "💀"

            content += f"\n{rate_emoji} Banned by **{rate:.0%}** ({stats.num_banned}/{num_responses})"

            reject_reasons = [
                (reject_reason.value, amount)
                for reject_reason, amount in stats.reject_reasons.items()
            ]
            reject_reasons.append(
                ("Unbanned", stats.num_rejected - sum(stats.reject_reasons.values()))
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

    if bm_rcon_url:
        try:
            bm_rcon_url = validate_url(bm_rcon_url, strict=True)
        except ValueError:
            pass
        else:
            links.append(format_url("Battlemetrics", bm_rcon_url))

    links.append(
        format_url("HLLRecords", f"https://hllrecords.com/profiles/{player.player_id}")
    )

    content += "\nView on " + " | ".join(links)
    if avatar_url:
        container.add_item(
            discord.ui.Section(
                discord.ui.TextDisplay(content),
                accessory=discord.ui.Thumbnail(avatar_url),
            )
        )
    else:
        container.add_item(discord.ui.TextDisplay(content))


async def get_player_avatar_urls(
    players: Sequence[schemas._PlayerReportBase],
) -> list[str | None]:
    # Get player avatars
    try:
        return await asyncio.wait_for(
            asyncio.gather(
                *(get_steam_avatar_url(player.player_id) for player in players)
            ),
            timeout=1.5,
        )
    except TimeoutError:
        return [None] * len(players)


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

    container_add_reasons(container, report)
    container.add_item(
        discord.ui.Separator(visible=False, spacing=discord.SeparatorSpacing.small)
    )
    container_add_description(container, report)
    container_add_attachments(container, report)

    player_avatar_urls = await get_player_avatar_urls(report.players)

    # Reported player(s)
    for i, player in enumerate(report.players):
        response = responses[i] if responses else None

        container_add_player(
            container,
            report,
            player,
            rank=i + 1,
            avatar_url=player_avatar_urls[i],
            response=response,
            stats=stats.get(player.id) if stats else None,
            with_eos_ids=with_eos_ids,
        )

        # Buttons
        if player_action_row_factory:
            player_action_row = player_action_row_factory(player, response)
            if player_action_row:
                container.add_item(player_action_row)

        # Responded by
        if response and response.responded_by:
            editor_name = (
                response.responded_by
                if get_user_id_from_mention(response.responded_by)
                else f"**{esc_md(response.responded_by)}**"
            )
            content = f"\n-# Reviewed by {editor_name}"
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
    community_url = report.token.community.contact_url
    if (
        not community_url.startswith(("http://", "https://"))
        and " " not in community_url
    ):
        community_url = "https://" + community_url

    content = (
        f"-# Reported by <@{report.token.admin_id}>"
        f" of [{report.token.community.name}]({community_url})"
        f" on {discord.utils.format_dt(report.created_at, 'f')}"
    )

    if report.edited_by or report.edited_at:
        content += "\nLast edited"
        if report.edited_by:
            editor_name = (
                report.edited_by
                if get_user_id_from_mention(report.edited_by)
                else f"**{esc_md(report.edited_by)}**"
            )
            content = f"\n-# Reviewed by {editor_name}"
            content += f" by {report.edited_by}"
        if report.edited_at:
            content += f" on {discord.utils.format_dt(report.edited_at, 'f')}"

    tags = (
        f"-# {get_game_pill(report.game)} {get_platform_pill(report.platforms_bitflag)}"
    )

    if refresh_button:
        view.add_item(
            discord.ui.Section(
                discord.ui.TextDisplay(content),
                discord.ui.TextDisplay(tags),
                accessory=refresh_button,
            )
        )
    else:
        view.add_item(discord.ui.TextDisplay(content))
        view.add_item(discord.ui.TextDisplay(tags))

    return view
