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
        user = await bot.get_or_fetch_user(admin.discord_id)
        return user.nick or user.display_name
    except:
        return admin.name


async def grant_admin_role(user_id: int):
    admin_role, owner_role = bot.get_admin_roles()
    user = await bot.get_or_fetch_user(user_id)
    await user.add_roles(admin_role)
    await user.remove_roles(owner_role)

async def grant_owner_role(user_id: int):
    admin_role, owner_role = bot.get_admin_roles()
    user = await bot.get_or_fetch_user(user_id)
    await user.add_roles(owner_role)
    await user.remove_roles(admin_role)

async def revoke_admin_roles(user_id: int):
    admin_role, owner_role = bot.get_admin_roles()
    user = await bot.get_or_fetch_user(user_id)
    await user.remove_roles(admin_role, owner_role)

