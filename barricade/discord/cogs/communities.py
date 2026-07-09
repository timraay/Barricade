from typing import TYPE_CHECKING

import discord
from discord import Interaction, app_commands
from discord.ext import commands

from barricade import schemas
from barricade.constants import DISCORD_GUILD_ID
from barricade.crud.communities import get_community_by_admin_id, get_community_by_id
from barricade.db import session_factory
from barricade.discord.autocomplete import atcp_community
from barricade.discord.utils import CustomException
from barricade.discord.views.channel_confirmation import (
    UpdateGuildConfirmationView,
    get_admin,
)
from barricade.discord.views.community_config import get_community_config_view
from barricade.discord.views.community_overview import CommunityOverviewView
from barricade.discord.views.integration_management import IntegrationManagementView

if TYPE_CHECKING:
    from barricade.discord.bot import Bot


async def assert_channel_permissions(channel: discord.TextChannel):
    required_perms = discord.Permissions(
        send_messages=True,
        read_messages=True,
        read_message_history=True,
        embed_links=True,
    )
    if not channel.permissions_for(channel.guild.me).is_superset(required_perms):
        raise CustomException(
            "Cannot read from and/or send messages to this channel!",
            (
                "Give the bot all of the following permissions and try again:"
                "\n"
                "\n- View Channel"
                "\n- Read Message History"
                "\n- Send Messages"
                "\n- Embed Links"
            ),
        )


class CommunitiesCog(commands.Cog):
    config_group = app_commands.Group(
        name="config",
        description="Configure community settings",
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )
    config_disable_group = app_commands.Group(
        name="disable",
        description="Disable certain features",
        parent=config_group,
    )

    def __init__(self, bot: "Bot"):
        self.bot = bot

    @config_group.command(
        name="integrations",
        description="Enable, disable, or configure your integrations",
    )
    async def manage_integrations(self, interaction: Interaction):
        async with session_factory() as db:
            # Make sure the user is part of a community
            db_admin = await get_admin(db, interaction.user.id)
            assert db_admin.community is not None

            community_id = db_admin.community.id

            db.expire(db_admin)
            db_community = await get_community_by_id(db, community_id)
            community = schemas.Community.model_validate(db_community)
            view = IntegrationManagementView(community)
            await view.send(interaction)

    @config_group.command(
        name="update-guild",
        description="Move your configurations over to this Discord server",
    )
    async def update_guild(self, interaction: Interaction):
        async with session_factory() as db:
            # Make sure the user is part of a community
            await get_admin(db, interaction.user.id)
            assert interaction.guild is not None
            view = UpdateGuildConfirmationView(interaction.guild)
            await view.send(interaction)

    @config_group.command(
        name="v2", description="See all your configured settings (v2)"
    )
    async def view_community_config_v2(self, interaction: Interaction):
        async with session_factory() as db:
            # Make sure the user is part of a community
            db_admin = await get_admin(db, interaction.user.id)
            assert db_admin.community is not None

            community_id = db_admin.community.id

            db.expire(db_admin)
            db_community = await get_community_by_id(db, community_id)

        community = schemas.Community.model_validate(db_community)
        view = await get_community_config_view(community)
        await interaction.response.send_message(view=view, ephemeral=True)

    @app_commands.command(
        name="community", description="Get information about a community"
    )
    @app_commands.guilds(DISCORD_GUILD_ID)
    @app_commands.autocomplete(community_id=atcp_community)
    @app_commands.describe(
        community_id="The name of a community",
        user="An admin of a community",
    )
    @app_commands.rename(community_id="community", user="admin")
    async def get_community_overview(
        self,
        interaction: Interaction,
        community_id: int | None = None,
        user: discord.Member | None = None,
    ):
        async with session_factory() as db:
            if community_id:
                db_community = await get_community_by_id(db, community_id)
                if not db_community:
                    raise CustomException("This community does not exist!")
            elif user:
                db_community = await get_community_by_admin_id(db, user.id)
                if not db_community:
                    raise CustomException("User is not an admin of a community!")
            else:
                db_community = await get_community_by_admin_id(db, interaction.user.id)
                if not db_community:
                    raise CustomException(
                        "You are not an admin of a community!",
                        "Specify a community or user to look for other communities.",
                    )

            community = schemas.Community.model_validate(db_community)

        assert isinstance(interaction.user, discord.Member)
        view = CommunityOverviewView(community, interaction.user)
        await view.send(interaction)


async def setup(bot: "Bot"):
    await bot.add_cog(CommunitiesCog(bot))
