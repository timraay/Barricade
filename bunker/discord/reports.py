import discord
from discord.utils import escape_markdown as esc_md

from bunker import schemas
from bunker.constants import DISCORD_REPORTS_CHANNEL_ID
from bunker.discord.bot import bot
from bunker.discord.communities import get_admin_name
from bunker.discord.utils import format_url
from bunker.enums import Emojis, ReportReasonFlag
from bunker.utils import get_player_id_type, PlayerIDType

def get_report_channel():
    return bot.primary_guild.get_channel(DISCORD_REPORTS_CHANNEL_ID)


async def get_report_embed(
        report: schemas.ReportWithToken,
        stats: dict[int, schemas.ResponseStats] = None,
        with_footer: bool = True
) -> discord.Embed:
    embed = discord.Embed(
        colour=discord.Colour.dark_theme(),
        description=esc_md(report.body),
    )
    embed.set_author(
        icon_url=bot.user.avatar.url,
        name="\n".join(
            ReportReasonFlag(report.reasons_bitflag).to_list(report.reasons_custom, with_emoji=True)
        )
    )

    for i, player in enumerate(report.players, 1):
        player_id_type = get_player_id_type(player.player_id)
        is_steam = player_id_type == PlayerIDType.STEAM_64_ID

        value = f"{Emojis.STEAM if is_steam else Emojis.XBOX} *`{player.player_id}`*"

        if stats and (stat := stats.get(player.id)):
            num_responses = stat.num_banned + stat.num_rejected
            if num_responses:
                rate = stat.num_banned / num_responses
                if rate >= 0.9:
                    emoji = Emojis.TICK_YES
                elif rate >= 0.7:
                    emoji = Emojis.TICK_MAYBE
                elif rate >= 0.5 or num_responses <= 3:
                    emoji = Emojis.TICK_NO
                else:
                    emoji = "💀"

                value += f"\n{emoji} Banned by **{rate:.0%}** ({num_responses})"

        if player_id_type == PlayerIDType.STEAM_64_ID:
            value += "\n" + format_url("View on Steam", f"https://steamcommunity.com/profiles/{player.player_id}")

        bm_rcon_url = player.player.bm_rcon_url
        if bm_rcon_url:
            value += "\n" + format_url("View on Battlemetrics", bm_rcon_url)

        embed.add_field(
            name=f"**`{i}.`** {esc_md(player.player_name)}",
            value=value,
            inline=True
        )

    if with_footer:
        try:
            user = await bot.get_or_fetch_member(report.token.admin_id)
        except discord.NotFound:
            avatar_url = None
        else:
            avatar_url = user.avatar.url

        admin_name = await get_admin_name(report.token.admin)

        embed.set_footer(
            text=f"Report by {admin_name} of {report.token.community.name} • {report.token.community.contact_url}",
            icon_url=avatar_url
        )

    return embed
