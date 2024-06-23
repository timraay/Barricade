from discord import NotFound
from bunker import schemas
from bunker.constants import DISCORD_ADMIN_ROLE_ID, DISCORD_OWNER_ROLE_ID
from bunker.discord.bot import bot

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

def get_forward_channel(community: schemas.CommunityRef):
    guild = bot.get_guild(community.forward_guild_id)
    if not guild:
        return
    channel = guild.get_channel(community.forward_channel_id)
    return channel
