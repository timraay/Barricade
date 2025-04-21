import logging
from typing import TYPE_CHECKING

import discord
from discord import Interaction, RawMemberRemoveEvent
from discord import app_commands
from discord.ext import commands
from discord.utils import escape_markdown as esc_md

from barricade import schemas
from barricade.db import session_factory
from barricade.constants import MAX_ADMIN_LIMIT, DISCORD_GUILD_ID
from barricade.crud.communities import abandon_community, admin_leave_community, get_admin_by_id, transfer_ownership
from barricade.discord.communities import update_user_roles
from barricade.discord.utils import CustomException, get_command_mention
from barricade.discord.views.admin_confirmation import (
    AdminAddConfirmationView,
    AdminRemoveConfirmationView,
    OwnershipTransferConfirmationView,
    LeaveCommunityConfirmationView
)
from barricade.logger import get_logger

if TYPE_CHECKING:
    from barricade.discord.bot import Bot

class AdminsCog(commands.Cog):
    def __init__(self, bot: 'Bot'):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        logger = logging

        try:
            # Only run if user joins primary guild
            if member.guild.id != DISCORD_GUILD_ID:
                return
            
            async with session_factory() as db:
                db_admin = await get_admin_by_id(db, discord_id=member.id)
                # Return if member is not an admin of any community
                if not db_admin or not db_admin.community:
                    return
                
                logger = get_logger(db_admin.community.id)
                
                admin = schemas.Admin.model_validate(db_admin)
                assert admin.community is not None
                
                # Warning because in theory this shouldn't be possible
                logger.warning(
                    "Admin %s (%s) rejoined primary guild, granting them their roles",
                    db_admin.name, db_admin.discord_id
                )

                # Grant roles
                await update_user_roles(member.id, community=admin.community)
        except:
            # discord.py would have silently eaten the exception
            logger.exception("Failed to invoke \"on_member_join\" event")

    @commands.Cog.listener()
    async def on_raw_member_remove(self, payload: RawMemberRemoveEvent):
        logger = logging

        try:
            # Only run if user joins primary guild
            if payload.guild_id != DISCORD_GUILD_ID:
                return

            async with session_factory.begin() as db:
                db_admin = await get_admin_by_id(db, discord_id=payload.user.id)
                # Return if member is not an admin of any community
                if not db_admin or not db_admin.community:
                    return
                
                db_community = db_admin.community
                community = schemas.CommunityRef.model_validate(db_community)

                logger = get_logger(community.id)
                
                if db_admin.owned_community:
                    # The user is the community owner
                    await db_community.awaitable_attrs.admins
                    if len(db_community.admins) > 1:
                        # If there's other admins in the community, transfer ownership to
                        # an arbitrary one and then remove them
                        db_new_owner = next(
                            admin
                            for admin in db_community.admins
                            if admin.discord_id != payload.user.id
                        )
                        new_owner = schemas.AdminRef.model_validate(db_new_owner)

                        logger.info(
                            "Owner %s (%s) left primary guild, transferring ownership to %s (%s)",
                            db_admin.name, db_admin.discord_id, new_owner.name, new_owner.discord_id
                        )

                        db.expunge(db_community)
                        db.expunge(db_new_owner)
                        await transfer_ownership(
                            db, community.id, new_owner.discord_id,
                            by=payload.user, # type: ignore
                        )

                        await db.refresh(db_admin)
                        await admin_leave_community(
                            db, db_admin,
                            by=payload.user, # type: ignore
                        )

                    else:
                        # If no admins remain, abandon the community instead
                        logger.info(
                            "Owner %s (%s) left primary guild, community was abandoned",
                            db_admin.name, db_admin.discord_id
                        )
                        await abandon_community(
                            db, community.id,
                            by=payload.user, # type: ignore
                        )

                else:
                    logger.info(
                        "Admin %s (%s) left primary guild, revoking their admin priviliges",
                        db_admin.name, db_admin.discord_id
                    )
                    # If the user is just an admin, simply remove them
                    await admin_leave_community(
                        db, db_admin,
                        by=payload.user, # type: ignore
                    )
        except:
            # discord.py would have silently eaten the exception
            logger.exception("Failed to invoke \"on_raw_member_remove\" event")
    
    @app_commands.command(name="add-admin", description="Add one of your community's admins to the Bunker")
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
                           f" {await get_command_mention(interaction.client.tree, 'leave-community', guild_only=True)}" # type: ignore
                           " command."
                        )
                    )
            
            if len(await owner.owned_community.awaitable_attrs.admins) > MAX_ADMIN_LIMIT:
                raise CustomException(
                    "You've hit the limit of admins allowed per community!",
                    (
                        f"Each community is only allowed up to {MAX_ADMIN_LIMIT} admins,"
                        " excluding the owner. You need to remove an existing admin before"
                        " you can add a new one."
                    )
                )
            
            view = AdminAddConfirmationView(owner.owned_community, user)
            await view.send(interaction)

    @app_commands.command(name="remove-admin", description="Remove an admin's access from the Bunker")
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
                       f"Use {await get_command_mention(interaction.client.tree, 'transfer-ownership', guild_only=True)} to" # type: ignore
                       f" transfer ownership, then {await get_command_mention(interaction.client.tree, 'leave-community', guild_only=True)}" # type: ignore
                        " to leave."
                    )
                )

            admin = await get_admin_by_id(db, user.id)
            if not admin or not admin.community or admin.community_id != owner.community_id:
                raise CustomException(
                    f"{esc_md(user.nick or user.display_name)} is not an admin of your community!"
                )
            
            view = AdminRemoveConfirmationView(admin.community, user)
            await view.send(interaction)

    @app_commands.command(name="transfer-ownership", description="Transfer ownership to another admin")
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
            if not admin or admin.community is None:
                raise CustomException(
                    f"{esc_md(user.nick or user.display_name)} is not part of {esc_md(owner.owned_community.name)}!",
                    (
                        f"Use {await get_command_mention(interaction.client.tree, 'add-admin', guild_only=True)}" # type: ignore
                        " first to add them to your community, before transfering ownership to them."
                    )
                )
            # Make sure admin is part of the community
            elif admin.community_id != owner.community_id:
                raise CustomException(
                    f"{esc_md(user.nick or user.display_name)} already is part of another community!"
                )
            
            view = OwnershipTransferConfirmationView(owner.owned_community, user)
            await view.send(interaction)

    @app_commands.command(name="leave-community", description="Remove your admin access from the Bunker")
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
                        f"Use {await get_command_mention(interaction.client.tree, 'transfer-ownership', guild_only=True)}" # type: ignore
                        " to transfer ownership to another community admin."
                    )
                )
            
            view = LeaveCommunityConfirmationView(admin.community, interaction.user) # type: ignore
            await view.send(interaction)
    
async def setup(bot: 'Bot'):
    await bot.add_cog(AdminsCog(bot))
