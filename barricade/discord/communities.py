from collections.abc import Callable

import discord

from barricade import schemas
from barricade.constants import (
    DISCORD_ADMIN_ROLE_ID,
    DISCORD_CONSOLE_ROLE_ID,
    DISCORD_OWNER_ROLE_ID,
    DISCORD_PC_ROLE_ID,
)
from barricade.discord.bot import bot
from barricade.discord.utils import CustomException
from barricade.enums import Game
from barricade.logger import get_logger
from barricade.utils import game_switch, safe_create_task


def get_admin_roles() -> tuple[discord.Role, discord.Role, discord.Role, discord.Role]:
    roles = []
    for role_id, role_name in (
        (DISCORD_ADMIN_ROLE_ID, "Admin"),
        (DISCORD_OWNER_ROLE_ID, "Owner"),
        (DISCORD_PC_ROLE_ID, "PC"),
        (DISCORD_CONSOLE_ROLE_ID, "Console"),
    ):
        role = bot.primary_guild.get_role(role_id)
        if not role:
            raise RuntimeError(f"{role_name} role not found")
        roles.append(role)
    return tuple(roles)


async def get_admin_name(admin: schemas.AdminRef):
    try:
        user = await bot.get_or_fetch_member(admin.discord_id)
        return user.nick or user.display_name
    except Exception:
        return admin.name


async def update_user_roles(
    user_id: int, community: schemas.CommunityRef, strict: bool = True
):
    admin_role, owner_role, pc_role, console_role = get_admin_roles()
    user = await bot.get_or_fetch_member(user_id, strict=strict)
    if not user:
        return False

    to_add: list[discord.Role] = []
    to_remove: list[discord.Role] = []

    if user_id == community.owner_id:
        to_add.append(owner_role)
        to_remove.append(admin_role)
    else:
        to_add.append(admin_role)
        to_remove.append(owner_role)

    if community.is_pc:
        to_add.append(pc_role)
    else:
        to_remove.append(pc_role)

    if community.is_console:
        to_add.append(console_role)
    else:
        to_remove.append(console_role)

    await user.add_roles(*to_add)
    await user.remove_roles(*to_remove)
    return True


async def revoke_user_roles(user_id: int, strict: bool = False):
    roles = get_admin_roles()
    user = await bot.get_or_fetch_member(user_id, strict=strict)
    if not user:
        return False
    await user.remove_roles(*roles)
    return True


def get_text_channel(
    guild_id: int | None, channel_id: int | None
) -> discord.TextChannel | None:
    if not guild_id or not channel_id:
        return

    guild = bot.get_guild(guild_id)
    if not guild:
        return None

    channel = guild.get_channel(channel_id)
    if channel and not isinstance(channel, discord.TextChannel):
        raise RuntimeError(f"Channel {channel!r} is not a TextChannel")

    return channel


def get_reports_channel(
    community: schemas.CommunityRef, game: Game
) -> discord.TextChannel | None:
    reports_channel_id = game_switch(
        game, community.hll_reports_channel_id, community.hllv_reports_channel_id
    )
    return get_text_channel(community.guild_id, reports_channel_id)


def get_confirmations_channel(
    community: schemas.CommunityRef, game: Game
) -> discord.TextChannel | None:
    confirmations_channel_id = game_switch(
        game,
        community.hll_confirmations_channel_id,
        community.hllv_confirmations_channel_id,
    )
    if confirmations_channel_id is None:
        return get_reports_channel(community, game)

    return get_text_channel(community.guild_id, confirmations_channel_id)


def get_alerts_channel(
    community: schemas.CommunityRef, game: Game
) -> discord.TextChannel | None:
    alerts_channel_id = game_switch(
        game, community.hll_alerts_channel_id, community.hllv_alerts_channel_id
    )
    if alerts_channel_id is None:
        return get_reports_channel(community, game)

    return get_text_channel(community.guild_id, alerts_channel_id)


def _get_role_mention(role_id: int | None) -> str | None:
    if role_id:
        return f"<@&{role_id}>"
    else:
        return None


def get_admin_role_mention(community: schemas.CommunityRef, game: Game) -> str | None:
    role_id = game_switch(
        game, community.hll_admin_role_id, community.hllv_admin_role_id
    )
    return _get_role_mention(role_id)


def get_alerts_role_mention(community: schemas.CommunityRef, game: Game) -> str | None:
    role_id = game_switch(
        game, community.hll_alerts_role_id, community.hllv_alerts_role_id
    )
    if role_id is None:
        return get_admin_role_mention(community, game)
    return _get_role_mention(role_id)


def safe_send_to_community(
    community: schemas.CommunityRef,
    game: Game | None,
    *args,
    channel_fn: Callable[
        [schemas.CommunityRef, Game], discord.TextChannel | None
    ] = get_reports_channel,
    **kwargs,
):
    channels: list[discord.TextChannel] = []
    if game:
        # Find single channel
        channel = channel_fn(community, game)
        if channel:
            channels.append(channel)
    else:
        # Find all channels. Remove duplicates.
        for game in Game:
            channel = channel_fn(community, game)
            if channel and channel not in channels:
                channels.append(channel)

    for channel in channels:
        safe_create_task(
            channel.send(*args, **kwargs),
            err_msg=f"Failed to send message to {community!r}",
            name=f"communitymessage_{community.id}",
            logger=get_logger(community.id),
        )


async def assert_has_admin_role(
    member: discord.Member, community: schemas.CommunityRef
):
    # Make sure user has the Admin role
    if not community.hll_admin_role_id:
        raise CustomException(
            "You are not permitted to do that!",
            f"Ask <@{community.owner_id}> to configure an Admin role.",
        )
    if not discord.utils.get(member.roles, id=community.hll_admin_role_id):  # type: ignore
        raise CustomException(
            "You are not permitted to do that!",
            "You do not have this community's configured Admin role.",
        )
