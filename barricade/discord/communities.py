import discord
from barricade import schemas
from barricade.constants import DISCORD_ADMIN_ROLE_ID, DISCORD_OWNER_ROLE_ID
from barricade.discord.bot import bot
from barricade.utils import safe_create_task

def get_admin_roles():
    admin_role = bot.primary_guild.get_role(DISCORD_ADMIN_ROLE_ID)
    if not admin_role:
        raise RuntimeError("Admin role not found")
    owner_role = bot.primary_guild.get_role(DISCORD_OWNER_ROLE_ID)
    if not owner_role:
        raise RuntimeError("Owner role not found")
    return admin_role, owner_role


async def get_admin_name(admin: schemas.AdminRef):
    try:
        user = await bot.get_or_fetch_member(admin.discord_id)
        return user.nick or user.display_name
    except:
        return admin.name


async def grant_admin_role(user_id: int, strict: bool = True):
    admin_role, owner_role = get_admin_roles()
    user = await bot.get_or_fetch_member(user_id, strict=strict)
    if not user:
        return False
    await user.add_roles(admin_role)
    await user.remove_roles(owner_role)
    return True

async def grant_owner_role(user_id: int, strict: bool = True):
    admin_role, owner_role = get_admin_roles()
    user = await bot.get_or_fetch_member(user_id, strict=strict)
    if not user:
        return False
    await user.add_roles(owner_role)
    await user.remove_roles(admin_role)
    return True

async def revoke_admin_roles(user_id: int, strict: bool = True):
    admin_role, owner_role = get_admin_roles()
    user = await bot.get_or_fetch_member(user_id, strict=strict)
    if not user:
        return False
    await user.remove_roles(admin_role, owner_role)
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