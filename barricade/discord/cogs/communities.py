from typing import TYPE_CHECKING

import discord
from discord import Interaction
from discord import app_commands
from discord.ext import commands

from barricade import schemas
from barricade.db import session_factory
from barricade.constants import DISCORD_GUILD_ID
from barricade.crud.communities import get_admin_by_id, get_community_by_id
from barricade.discord.autocomplete import atcp_community
from barricade.discord.communities import get_forward_channel
from barricade.discord.utils import CustomException, get_command_mention
from barricade.discord.views.admin_role_confirmation import AdminRoleConfirmationView
from barricade.discord.views.community_overview import CommunityOverviewView
from barricade.discord.views.integration_management import IntegrationManagementView
from barricade.discord.views.report_channel_confirmation import ReportChannelConfirmationView

if TYPE_CHECKING:
    from barricade.discord.bot import Bot

class CommunitiesCog(commands.Cog):
    config_group = app_commands.Group(
        name="config",
        description="Configure community settings",
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: 'Bot'):
        self.bot = bot

    @app_commands.command(name="integrations", description="Enable, disable, or configure your integrations")
    @app_commands.guilds(DISCORD_GUILD_ID)
    @app_commands.default_permissions(manage_guild=True)
    async def manage_integrations(self, interaction: Interaction):
        async with session_factory() as db:
            # Make sure the user owns a community
            db_owner = await get_admin_by_id(db, interaction.user.id)
            if not db_owner or not db_owner.owned_community:
                raise CustomException(
                    "You need to be a community owner to do this!"
                )
            
            community_id = db_owner.owned_community.id
            
            db.expire(db_owner)
            db_community = await get_community_by_id(db, community_id)
            community = schemas.Community.model_validate(db_community)
            view = IntegrationManagementView(community)
            await view.send(interaction)

    @config_group.command(name="reports-channel", description="Set a channel as your community's report feed")
    async def set_reports_channel(self, interaction: Interaction, channel: discord.TextChannel):
        async with session_factory() as db:
            # Make sure the user owns a community
            db_owner = await get_admin_by_id(db, interaction.user.id)
            if not db_owner or not db_owner.owned_community:
                raise CustomException(
                    "You need to be a community owner to do this!"
                )
            
            if channel.permissions_for(channel.guild.default_role).read_messages:
                raise CustomException(
                    "This channel is publicly visible!",
                    "Report feeds should be private and only accessible by admins."
                )
            
            required_perms = discord.Permissions(send_messages=True, read_messages=True, read_message_history=True)
            if not channel.permissions_for(channel.guild.me).is_superset(required_perms):
                raise CustomException(
                    "Cannot read from and/or send messages to this channel!",
                    (
                        f"Give the bot all of the following permissions and try again:"
                        "\n"
                        "\n- View Channel"
                        "\n- Read Message History"
                        "\n- Send Messages"
                    )
                )
            
            view = ReportChannelConfirmationView(channel)
            await view.send(interaction)

    @config_group.command(name="admin-role", description="Set a role to identify your admins with")
    async def set_admin_role(self, interaction: Interaction, role: discord.Role):
        async with session_factory() as db:
            # Make sure the user owns a community
            db_owner = await get_admin_by_id(db, interaction.user.id)
            if not db_owner or not db_owner.owned_community:
                raise CustomException(
                    "You need to be a community owner to do this!"
                )
            
            if db_owner.community.forward_guild_id:
                guild = self.bot.get_guild(db_owner.community.forward_guild_id)
                if guild and guild != role.guild:
                    raise CustomException(
                        "Role must be from the same server as your Reports feed!",
                        "Your Reports feed is in a different Discord server. If you want to move to this server, first move your feed."
                    )

            view = AdminRoleConfirmationView(role)
            await view.send(interaction)

    @config_group.command(name="view", description="See all your configured settings")
    async def view_community_config(self, interaction: Interaction):
        async with session_factory() as db:
            # Make sure the user owns a community
            db_owner = await get_admin_by_id(db, interaction.user.id)
            if not db_owner or not db_owner.owned_community:
                raise CustomException(
                    "You need to be a community owner to do this!"
                )
            
            community = schemas.CommunityRef.model_validate(db_owner.community)
            channel = get_forward_channel(community)

            embed = discord.Embed()
            embed.add_field(
                name="Reports feed",
                value=(
                    f"-# *{await get_command_mention(self.bot.tree, 'config', 'reports-channel')}*"
                    f"\n> -# The text channel where you receive new reports."
                    f"\n- {channel.mention if channel else 'Unknown' if db_owner.community.forward_channel_id else 'None'}"
                ),
                inline=True
            )
            embed.add_field(
                name="Admin role",
                value=(
                    f"-# *{await get_command_mention(self.bot.tree, 'config', 'admin-role')}*"
                    f"\n> -# The role that can review reports."
                    f"\n- {'<@&'+str(db_owner.community.admin_role_id)+'>' if db_owner.community.admin_role_id else 'None'}"
                ),
                inline=True
            )

            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="community", description="Get information about a community")
    @app_commands.guilds(DISCORD_GUILD_ID)
    @app_commands.autocomplete(community=atcp_community)
    @app_commands.describe(community="The name of a community")
    async def get_community_overview(self, interaction: Interaction, community: int):
        async with session_factory() as db:
            db_community = await get_community_by_id(db, community)
            if not db_community:
                raise CustomException(
                    "This community does not exist!"
                )
            _community = schemas.Community.model_validate(db_community)
        view = CommunityOverviewView(_community, interaction.user) # type: ignore
        await view.send(interaction)

async def setup(bot: 'Bot'):
    await bot.add_cog(CommunitiesCog(bot))
