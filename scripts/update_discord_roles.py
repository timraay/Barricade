import asyncio

import discord
from barricade import schemas
from barricade.constants import DISCORD_BOT_TOKEN
from barricade.crud.communities import get_admin_by_id
from barricade.discord import bot
from barricade.discord.communities import get_admin_roles, revoke_user_roles, update_user_roles
from barricade.utils import safe_create_task
from barricade.db import session_factory


async def main():
    # Requires the "members" intent.
    # This is a priviliged intent that needs to be explicitly enabled on the Developer
    # portal, and needs to temporarily be enabled in barricade.discord.bot

    try:
        # Start the Discord bot
        await bot.login(DISCORD_BOT_TOKEN)
        safe_create_task(bot.connect(reconnect=True))
        await bot.wait_until_ready()

        admin_role, owner_role, *_ = get_admin_roles()
        
        async with session_factory() as db:
            async for member in bot.primary_guild.fetch_members(limit=None):
                if admin_role in member.roles or owner_role in member.roles:
                    print(member.display_name)

                    db_admin = await get_admin_by_id(db, member.id)
                    admin = schemas.Admin.model_validate(db_admin)

                    if admin.community:
                        await update_user_roles(member.id, admin.community)
                    else:
                        await revoke_user_roles(member.id)

    finally:
        # Close the bot
        if not bot.is_closed():
            await bot.close()

if __name__ == '__main__':
    asyncio.run(main())