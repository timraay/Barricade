from datetime import datetime, timezone
import discord
from discord.utils import escape_markdown as esc_md
import logging

from pydantic import BaseModel

from barricade import schemas
from barricade.constants import DISCORD_AUDIT_CHANNEL_ID, DISCORD_REPORTS_CHANNEL_ID
from barricade.discord.reports import get_report_embed
from .bot import bot

async def set_footer(embed: discord.Embed, user_id: int, by: str | discord.User | None = None):
    if by:
        if isinstance(by, discord.User):
            return embed.set_footer(text=by.display_name, icon_url=by.display_avatar.url)
        else:
            return embed.set_footer(text=by)
    
    user = await bot.get_or_fetch_user(user_id)
    if user:
        embed.set_footer(
            icon_url=user.display_avatar,
            text=user.display_name,
        )
    else:
        embed.set_footer(
            text=str(user_id)
        )


def add_community_field(embed: discord.Embed, community: schemas.CommunityRef):
    return embed.add_field(
        name=f"Community (`#{community.id}`)",
        value=f"{esc_md(community.tag)} {esc_md(community.name)}"
    )

async def add_admin_field(embed: discord.Embed, admin: schemas.AdminRef, header: str = "Admin"):
    user = await bot.get_or_fetch_user(admin.discord_id)
    if not user:
        return embed.add_field(
            name=header,
            value=f"*User not found*\n`{admin.discord_id}`"
        )
    return embed.add_field(
        name=header,
        value=f"{esc_md(user.display_name)}\n{user.mention}"
    )

def add_payload_field(embed: discord.Embed, schema: BaseModel):
    return embed.add_field(
        name="Payload",
        value=f"```json\n{schema.model_dump_json(indent=2)[:1000]}\n```",
        inline=False,
    )

def get_payload_embed(schema: BaseModel):
    return discord.Embed(
        description=f"**Payload**\n```json\n{schema.model_dump_json(indent=2)[:4000]}\n```",
    )

def get_avatar_url(user_id: int):
    user = bot.get_user(user_id)
    if not user:
        return None
    return user.display_avatar.url


async def _audit(*embeds: discord.Embed):
    if not DISCORD_AUDIT_CHANNEL_ID:
        return
    channel = bot.get_channel(DISCORD_AUDIT_CHANNEL_ID)
    if not channel:
        logging.warn("Tried to send to audit but channel with ID %s could not be found", DISCORD_AUDIT_CHANNEL_ID)
        return
    elif not isinstance(channel, discord.TextChannel):
        logging.warn("Tried to send to audit but channel with ID %s is not a text channel", DISCORD_AUDIT_CHANNEL_ID)
        return

    try:
        await channel.send(embeds=embeds)
    except:
        logging.exception("Failed to audit message")


async def audit_community_create(
    community: schemas.Community,
    by: str | None = None,
):
    embed = discord.Embed(
        color=discord.Colour.green(),
        timestamp=datetime.now(tz=timezone.utc)
    ).set_author(
        icon_url=get_avatar_url(community.owner_id),
        name="Community created",
    )
    await set_footer(embed, community.owner_id, by)
    add_community_field(embed, community)
    add_payload_field(embed, schemas.CommunityRef(**community.model_dump()))
    await add_admin_field(embed, community.owner, "Owner")

    await _audit(embed)

async def audit_community_edit(
    community: schemas.Community,
    by: str | None = None,
):
    embed = discord.Embed(
        color=discord.Colour.yellow(),
        timestamp=datetime.now(tz=timezone.utc)
    ).set_author(
        icon_url=get_avatar_url(community.owner_id),
        name="Community created",
    )
    await set_footer(embed, community.owner_id, by)
    add_community_field(embed, community)
    add_payload_field(embed, schemas.CommunityRef(**community.model_dump()))
    await add_admin_field(embed, community.owner, "Owner")

    await _audit(embed)

async def audit_community_change_owner(
    old_owner: schemas.AdminRef,
    new_owner: schemas.Admin,
    by: str | None = None,
):
    embed = discord.Embed(
        color=discord.Colour.yellow(),
        timestamp=datetime.now(tz=timezone.utc)
    ).set_author(
        icon_url=get_avatar_url(new_owner.discord_id),
        name="Community ownership transferred",
    )
    await set_footer(embed, old_owner.discord_id, by)
    if new_owner.community:
        add_community_field(embed, new_owner.community)
    await add_admin_field(embed, old_owner, "Old Owner")
    await add_admin_field(embed, new_owner, "New Owner")

    await _audit(embed)

async def audit_community_admin_add(
    community: schemas.CommunityRef,
    admin: schemas.AdminRef,
    by: str | None = None,
):
    embed = discord.Embed(
        color=discord.Colour.green(),
        timestamp=datetime.now(tz=timezone.utc)
    ).set_author(
        icon_url=get_avatar_url(admin.discord_id),
        name="Admin added to community",
    )
    await set_footer(embed, community.owner_id, by)
    add_community_field(embed, community)
    await add_admin_field(embed, admin)

    await _audit(embed)

async def audit_community_admin_remove(
    community: schemas.CommunityRef,
    admin: schemas.AdminRef,
    by: str | discord.User | None = None,
):
    if isinstance(by, discord.User) and by.id == admin.discord_id:
        return await audit_community_admin_leave(community, admin)

    embed = discord.Embed(
        color=discord.Colour.dark_red(),
        timestamp=datetime.now(tz=timezone.utc)
    ).set_author(
        icon_url=get_avatar_url(admin.discord_id),
        name="Admin removed from community",
    )
    await set_footer(embed, community.owner_id, by)
    add_community_field(embed, community)
    await add_admin_field(embed, admin)

    await _audit(embed)

async def audit_community_admin_leave(
    community: schemas.CommunityRef,
    admin: schemas.AdminRef,
):
    embed = discord.Embed(
        color=discord.Colour.red(),
        timestamp=datetime.now(tz=timezone.utc)
    ).set_author(
        icon_url=get_avatar_url(admin.discord_id),
        name="Admin left community",
    )
    await set_footer(embed, admin.discord_id)
    add_community_field(embed, community)
    await add_admin_field(embed, admin)

    await _audit(embed)

async def audit_token_create(
    token: schemas.ReportTokenRef,
    by: str | None = None,
):
    embed = discord.Embed(
        color=discord.Colour.dark_blue(),
        timestamp=datetime.now(tz=timezone.utc)
    ).set_author(
        icon_url=get_avatar_url(token.admin_id),
        name="Token created",
    )
    await set_footer(embed, token.admin_id, by)
    add_community_field(embed, token.community)
    await add_admin_field(embed, token.admin)

    await _audit(embed)

async def audit_report_create(
    report: schemas.ReportWithToken,
    by: str | None = None,
):
    embed = discord.Embed(
        color=discord.Colour.blue(),
        timestamp=datetime.now(tz=timezone.utc)
    ).set_author(
        icon_url=get_avatar_url(report.token.admin_id),
        name="Report submitted",
    )
    await set_footer(embed, report.token.admin_id, by)
    add_community_field(embed, report.token.community)
    await add_admin_field(embed, report.token.admin)
    embed.add_field(
        name="Message",
        value=bot.get_partial_message(
            DISCORD_REPORTS_CHANNEL_ID,
            report.message_id
        ).jump_url
    )
    payload = get_payload_embed(schemas.SafeReportWithToken(**report.model_dump()))

    await _audit(embed, payload)

async def audit_report_edit(
    report: schemas.ReportWithToken,
    by: str | None = None,
):
    embed = discord.Embed(
        color=discord.Colour.blurple(),
        timestamp=datetime.now(tz=timezone.utc)
    ).set_author(
        icon_url=get_avatar_url(report.token.admin_id),
        name="Report edited",
    )
    await set_footer(embed, report.token.admin_id, by)
    add_community_field(embed, report.token.community)
    await add_admin_field(embed, report.token.admin)
    embed.add_field(
        name="Message",
        value=bot.get_partial_message(
            DISCORD_REPORTS_CHANNEL_ID,
            report.message_id
        ).jump_url
    )
    payload = get_payload_embed(schemas.SafeReportWithToken(**report.model_dump()))

    await _audit(embed, payload)

async def audit_report_delete(
    report: schemas.ReportWithToken,
    stats: dict[int, schemas.ResponseStats],
    by: str | None = None,
):
    embed = discord.Embed(
        color=discord.Colour.dark_purple(),
        timestamp=datetime.now(tz=timezone.utc)
    ).set_author(
        icon_url=get_avatar_url(report.token.admin_id),
        name="Report deleted",
    )
    await set_footer(embed, report.token.admin_id, by)
    add_community_field(embed, report.token.community)
    await add_admin_field(embed, report.token.admin)
    embed.add_field(
        name="Report",
        value="See below"
    )
    payload = get_payload_embed(schemas.SafeReportWithToken(**report.model_dump()))
    report_embed = await get_report_embed(report, stats, with_footer=False)

    await _audit(embed, payload, report_embed)
