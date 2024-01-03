from typing import TYPE_CHECKING

import discord
from discord import Interaction
from discord import app_commands
from discord.ext import commands
from discord.utils import escape_markdown as esc_md

from bunker.db import session_factory
from bunker.communities import get_admin_by_id
from bunker.constants import MAX_ADMIN_LIMIT, DISCORD_GUILD_ID
from bunker.discord.utils import CustomException, get_command_mention
from bunker.discord.views.admin_confirmation import (
    AdminAddConfirmationView,
    AdminRemoveConfirmationView,
    OwnershipTransferConfirmationView,
    LeaveCommunityConfirmationView
)

if TYPE_CHECKING:
    from bunker.discord.bot import Bot

class CommunitiesCog(commands.Cog):
    def __init__(self, bot: 'Bot'):
        self.bot = bot
    
    @app_commands.command(name="add_admin", description="Add one of your community's admins to the Bunker")
    @app_commands.guilds(DISCORD_GUILD_ID)
    @app_commands.default_permissions(manage_guild=True)
    async def add_admin_to_community(self, interaction: Interaction, user: discord.Member):
        async with session_factory() as db:
            # Make sure the user is a community owner
            owner = await get_admin_by_id(db, interaction.user.id)
            if not owner or not owner.owned_community:
                raise CustomException(
                    "You need to be a community owner to do this!",
                )
            
            admin = await get_admin_by_id(db, user.id)
            if admin:
                if admin.community_id == owner.community_id:
                    raise CustomException(
                        f"{esc_md(user.nick or user.display_name)} is already part of your community!"
                    )
                # Make sure admin isn't part of any other community yet
                if admin.community_id:
                    raise CustomException(
                        f"{esc_md(user.nick or user.display_name)} is already part of another community!",
                        (
                            "Ask them to leave their current community first by using the"
                           f" {get_command_mention(interaction.client.tree, 'leave_community')} command."
                        )
                    )
            
            if len(await owner.owned_community.awaitable_attrs.admins) >= MAX_ADMIN_LIMIT:
                raise CustomException(
                    "You've hit the limit of admins allowed per community!",
                    (
                        f"Each community is only allowed up to {MAX_ADMIN_LIMIT} admins,"
                        " including yourself. You need to remove an existing admin before"
                        " you can add a new one."
                    )
                )
            
            view = AdminAddConfirmationView(admin.community, user)
            await view.send(interaction)

    @app_commands.command(name="remove_admin", description="Remove an admin's access from the Bunker")
    @app_commands.guilds(DISCORD_GUILD_ID)
    @app_commands.default_permissions(manage_guild=True)
    async def remove_admin_from_community(self, interaction: Interaction, user: discord.Member):
        async with session_factory() as db:
            # Make sure the user is a community owner
            owner = await get_admin_by_id(db, interaction.user.id)
            if not owner or not owner.owned_community:
                raise CustomException(
                    "You need to be a community owner to do this!",
                )
            
            if user.id == interaction.user.id:
                raise CustomException(
                    f"You cannot remove yourself from your own community!",
                    (
                       f"Use {get_command_mention(interaction.client.tree, 'transfer_ownership')} to"
                       f" transfer ownership, then {get_command_mention(interaction.client.tree, 'leave_community')}"
                        " to leave."
                    )
                )

            admin = await get_admin_by_id(db, user.id)
            if not admin or admin.community_id != owner.community_id:
                raise CustomException(
                    f"{esc_md(user.nick or user.display_name)} is not an admin of your community!"
                )
            
            view = AdminRemoveConfirmationView(admin.community, user)
            await view.send(interaction)

    @app_commands.command(name="transfer_ownership", description="Transfer ownership to another admin")
    @app_commands.guilds(DISCORD_GUILD_ID)
    @app_commands.default_permissions(manage_guild=True)
    async def transfer_ownership_of_community(self, interaction: Interaction, user: discord.Member):
        async with session_factory() as db:
            # Make sure the user is a community owner
            owner = await get_admin_by_id(db, interaction.user.id)
            if not owner or not owner.owned_community:
                raise CustomException(
                    "You need to be a community owner to do this!",
                )
            
            # Make sure they're not transfering to themselves
            if user.id == interaction.user.id:
                raise CustomException(
                    "You can't transfer ownership to yourself!"
                )
            
            admin = await get_admin_by_id(db, user.id)

            # Make sure admin exists
            if not admin or admin.community_id is None:
                raise CustomException(
                    f"{esc_md(user.nick or user.display_name)} is not part of {esc_md(owner.community.name)}!",
                    (
                        f"Use {get_command_mention(interaction.client.tree, 'add_admin')} first to add them"
                        " to your community, before transfering ownership to them."
                    )
                )
            # Make sure admin is part of the community
            elif admin.community_id != owner.community_id:
                raise CustomException(
                    f"{esc_md(user.nick or user.display_name)} already is part of another community!"
                )
            
            view = OwnershipTransferConfirmationView(admin.community, user, owner)
            await view.send(interaction)

    @app_commands.command(name="leave_community", description="Remove your admin access from the Bunker")
    @app_commands.guilds(DISCORD_GUILD_ID)
    @app_commands.default_permissions(manage_guild=True)
    async def leave_community_as_admin(self, interaction: Interaction):
        async with session_factory() as db:
            admin = await get_admin_by_id(db, interaction.user.id)
            # Make sure the user is part of a community
            if not admin or not admin.community_id:
                raise CustomException("You can't leave a community without being part of one...!")
            # Make sure the user is not an owner
            if admin.owned_community:
                raise CustomException(
                    "You must transfer ownership first!",
                    (
                        f"Use {get_command_mention(interaction.client.tree, 'transfer_ownership')} to transfer"
                        " ownership to another community admin."
                    )
                )
            
            view = LeaveCommunityConfirmationView(admin.community, interaction.user)
            await view.send(interaction)

async def setup(bot: 'Bot'):
    await bot.add_cog(CommunitiesCog(bot))
