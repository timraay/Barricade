import discord
from barricade import schemas
from barricade.constants import DISCORD_ADMIN_ROLE_ID, DISCORD_OWNER_ROLE_ID, DISCORD_PC_ROLE_ID, DISCORD_CONSOLE_ROLE_ID
from barricade.discord.bot import bot
from barricade.utils import safe_create_task

def get_admin_roles() -> tuple[discord.Role, discord.Role, discord.Role, discord.Role]:
    roles = []
    for (role_id, role_name) in (
        (DISCORD_ADMIN_ROLE_ID, "Admin"),
        (DISCORD_OWNER_ROLE_ID, "Owner"),
        (DISCORD_PC_ROLE_ID, "PC"),
        (DISCORD_CONSOLE_ROLE_ID, "Console"),
    ):
        role = bot.primary_guild.get_role(role_id)
        if not role:
            raise RuntimeError("%s role not found" % role_name)
        roles.append(role)
    return tuple(roles)


async def get_admin_name(admin: schemas.AdminRef):
    try:
        user = await bot.get_or_fetch_member(admin.discord_id)
        return user.nick or user.display_name
    except:
        return admin.name


async def update_user_roles(user_id: int, community: schemas.CommunityRef, strict: bool = True):
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

def get_forward_channel(community: schemas.CommunityRef) -> discord.TextChannel | None:
    if not community.forward_guild_id or not community.forward_channel_id:
        return
    
    guild = bot.get_guild(community.forward_guild_id)
    if not guild:
        return
    
    channel = guild.get_channel(community.forward_channel_id)
    if channel and not isinstance(channel, discord.TextChannel):
        raise RuntimeError("Forward channel %r is not a TextChannel" % channel)
    return channel

def safe_send_to_community(community: schemas.CommunityRef, *args, **kwargs):
    channel = get_forward_channel(community)
    if not channel:
        return
    safe_create_task(channel.send(*args, **kwargs))
