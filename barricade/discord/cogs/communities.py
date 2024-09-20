from typing import TYPE_CHECKING

import discord
from discord import Interaction
from discord import app_commands
from discord.ext import commands
from sqlalchemy.ext.asyncio import AsyncSession

from barricade import schemas
from barricade.db import session_factory
from barricade.constants import DISCORD_GUILD_ID
from barricade.crud.communities import get_admin_by_id, get_community_by_id
from barricade.discord.autocomplete import atcp_community
from barricade.discord.communities import get_alerts_channel, get_confirmations_channel, get_forward_channel
from barricade.discord.utils import CustomException, get_command_mention
from barricade.discord.views.role_confirmation import AdminRoleConfirmationView, AlertsRoleConfirmationView
from barricade.discord.views.community_overview import CommunityOverviewView
from barricade.discord.views.integration_management import IntegrationManagementView
from barricade.discord.views.reasons_filter import ReasonsFilterView
from barricade.discord.views.channel_confirmation import AlertsChannelConfirmationView, ConfirmationsChannelConfirmationView, ReportChannelConfirmationView, UpdateGuildConfirmationView, assert_community_guild, get_admin

if TYPE_CHECKING:
    from barricade.discord.bot import Bot

async def assert_channel_permissions(channel: discord.TextChannel):
    required_perms = discord.Permissions(send_messages=True, read_messages=True, read_message_history=True, embed_links=True)
    if not channel.permissions_for(channel.guild.me).is_superset(required_perms):
        raise CustomException(
            "Cannot read from and/or send messages to this channel!",
            (
                f"Give the bot all of the following permissions and try again:"
                "\n"
                "\n- View Channel"
                "\n- Read Message History"
                "\n- Send Messages"
                "\n- Embed Links"
            )
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

    def __init__(self, bot: 'Bot'):
        self.bot = bot

    @config_group.command(name="integrations", description="Enable, disable, or configure your integrations")
    async def manage_integrations(self, interaction: Interaction):
        async with session_factory() as db:
            # Make sure the user is part of a community
            db_admin = await get_admin(db, interaction.user.id)
            
            community_id = db_admin.community.id
            
            db.expire(db_admin)
            db_community = await get_community_by_id(db, community_id)
            community = schemas.Community.model_validate(db_community)
            view = IntegrationManagementView(community)
            await view.send(interaction)
    
    @config_group.command(name="update-guild", description="Move your configurations over to this Discord server")
    async def update_guild(self, interaction: Interaction):
        async with session_factory() as db:
            # Make sure the user is part of a community
            db_admin = await get_admin(db, interaction.user.id)
            assert interaction.guild is not None
            view = UpdateGuildConfirmationView(interaction.guild)
            await view.send(interaction)

    @config_group.command(name="reports-channel", description="Set which channel to receive reports in")
    async def set_reports_channel(self, interaction: Interaction, channel: discord.TextChannel):
        async with session_factory() as db:
            # Make sure the user is part of a community
            db_admin = await get_admin(db, interaction.user.id)
            await assert_community_guild(db_admin.community, interaction)
            
            if channel.permissions_for(channel.guild.default_role).read_messages:
                raise CustomException(
                    "This channel is publicly visible!",
                    "Report feeds should be private and only accessible by admins."
                )
            
            await assert_channel_permissions(channel)
            
            view = ReportChannelConfirmationView(channel)
            await view.send(interaction)
    
    @config_disable_group.command(name="reports-channel", description="Stop receiving any reports")
    async def disable_reports_channel(self, interaction: Interaction):
        async with session_factory() as db:
            # Make sure the user is part of a community
            db_admin = await get_admin(db, interaction.user.id)
            await assert_community_guild(db_admin.community, interaction)
            
            view = ReportChannelConfirmationView(None)
            await view.send(interaction)

    @config_group.command(name="confirmations-channel", description="Set which channel to receive report confirmations in")
    async def set_confirmations_channel(self, interaction: Interaction, channel: discord.TextChannel):
        async with session_factory() as db:
            # Make sure the user is part of a community
            db_admin = await get_admin(db, interaction.user.id)
            await assert_community_guild(db_admin.community, interaction)
            
            if db_admin.community.forward_guild_id and db_admin.community.forward_guild_id != channel.guild.id:
                raise CustomException(
                    "Channel must be in the same server as your Reports feed!",
                    "Your Reports feed is in a different Discord server. If you want to move to this server, first move your feed."
                )
            
            if channel.permissions_for(channel.guild.default_role).read_messages:
                raise CustomException(
                    "This channel is publicly visible!",
                    "Confirmations feeds should be private and only accessible by admins."
                )
            
            await assert_channel_permissions(channel)
            
            view = ConfirmationsChannelConfirmationView(channel)
            await view.send(interaction)
    
    @config_disable_group.command(name="confirmations-channel", description="Receive report confirmations via DMs")
    async def disable_confirmations_channel(self, interaction: Interaction):
        async with session_factory() as db:
            # Make sure the user is part of a community
            db_admin = await get_admin(db, interaction.user.id)
            await assert_community_guild(db_admin.community, interaction)
            
            view = ConfirmationsChannelConfirmationView(None)
            await view.send(interaction)

    @config_group.command(name="alerts-channel", description="Set which channel to receive player alerts in")
    async def set_alerts_channel(self, interaction: Interaction, channel: discord.TextChannel):
        async with session_factory() as db:
            # Make sure the user is part of a community
            db_admin = await get_admin(db, interaction.user.id)
            await assert_community_guild(db_admin.community, interaction)
            
            if db_admin.community.forward_guild_id and db_admin.community.forward_guild_id != channel.guild.id:
                raise CustomException(
                    "Channel must be in the same server as your Reports feed!",
                    "Your Reports feed is in a different Discord server. If you want to move to this server, first move your feed."
                )
            
            if channel.permissions_for(channel.guild.default_role).read_messages:
                raise CustomException(
                    "This channel is publicly visible!",
                    "Alerts feeds should be private and only accessible by admins."
                )
            
            await assert_channel_permissions(channel)
            
            view = AlertsChannelConfirmationView(channel)
            await view.send(interaction)
    
    @config_disable_group.command(name="alerts-channel", description="Stop receiving any alerts")
    async def disable_alerts_channel(self, interaction: Interaction):
        async with session_factory() as db:
            # Make sure the user is part of a community
            db_admin = await get_admin(db, interaction.user.id)
            await assert_community_guild(db_admin.community, interaction)
            
            view = AlertsChannelConfirmationView(None)
            await view.send(interaction)

    @config_group.command(name="admin-role", description="Set a role to identify your admins with")
    async def set_admin_role(self, interaction: Interaction, role: discord.Role):
        async with session_factory() as db:
            # Make sure the user is part of a community
            db_admin = await get_admin(db, interaction.user.id)
            await assert_community_guild(db_admin.community, interaction)
            
            if db_admin.community.forward_guild_id:
                guild = self.bot.get_guild(db_admin.community.forward_guild_id)
                if guild and guild != role.guild:
                    raise CustomException(
                        "Role must be from the same server as your Reports feed!",
                        "Your Reports feed is in a different Discord server. If you want to move to this server, first move your feed."
                    )

            view = AdminRoleConfirmationView(role)
            await view.send(interaction)

    @config_group.command(name="alerts-role", description="Set a role to notify when an alert comes in")
    async def set_alerts_role(self, interaction: Interaction, role: discord.Role):
        async with session_factory() as db:
            # Make sure the user is part of a community
            db_admin = await get_admin(db, interaction.user.id)
            await assert_community_guild(db_admin.community, interaction)
            
            if db_admin.community.forward_guild_id:
                guild = self.bot.get_guild(db_admin.community.forward_guild_id)
                if guild and guild != role.guild:
                    raise CustomException(
                        "Role must be from the same server as your feeds!",
                        "Your feeds are in a different Discord server. If you want to move to this server, first move your Reports feed."
                    )

            view = AdminRoleConfirmationView(role)
            await view.send(interaction)
    
    @config_disable_group.command(name="alerts-role", description="Stop any roles from being notified by incoming alerts")
    async def disable_alerts_role(self, interaction: Interaction):
        async with session_factory() as db:
            # Make sure the user is part of a community
            db_admin = await get_admin(db, interaction.user.id)
            await assert_community_guild(db_admin.community, interaction)
            
            view = AlertsRoleConfirmationView(None)
            await view.send(interaction)

    @config_group.command(name="reports-filter", description="Select which categories of reports to receive")
    async def set_reports_filter(self, interaction: Interaction):
        async with session_factory() as db:
            # Make sure the user is part of a community
            db_admin = await get_admin(db, interaction.user.id)
            await assert_community_guild(db_admin.community, interaction)
            
            community = schemas.CommunityRef.model_validate(db_admin.community)
            
        view = ReasonsFilterView(community)
        await view.send(interaction)

    @config_group.command(name="view", description="See all your configured settings")
    async def view_community_config(self, interaction: Interaction):
        async with session_factory() as db:
            # Make sure the user is part of a community
            db_admin = await get_admin(db, interaction.user.id)
            community = schemas.CommunityRef.model_validate(db_admin.community)

            reports_channel = get_forward_channel(community)
            if reports_channel:
                reports_channel_mention = reports_channel.mention
            elif community.forward_channel_id:
                reports_channel_mention = "Unknown"
            else:
                reports_channel_mention = "None"

            confirmations_channel = get_confirmations_channel(community)
            if community.confirmations_channel_id is None:
                confirmations_channel_mention = "Same as **Reports feed**"
            elif confirmations_channel:
                confirmations_channel_mention = confirmations_channel.mention
            elif community.confirmations_channel_id:
                confirmations_channel_mention = "Unknown"
            else:
                confirmations_channel_mention = "None"

            alerts_channel = get_alerts_channel(community)
            if community.alerts_channel_id is None:
                alerts_channel_mention = "Same as **Reports feed**"
            elif alerts_channel:
                alerts_channel_mention = alerts_channel.mention
            elif community.alerts_channel_id:
                alerts_channel_mention = "Unknown"
            else:
                alerts_channel_mention = "None"

            if community.admin_role_id:
                admin_role_mention = f"<@&{community.admin_role_id}>"
            else:
                admin_role_mention = "None"

            if community.alerts_role_id is None:
                alerts_role_mention = "Same as **Admin role**"
            elif community.alerts_role_id:
                alerts_role_mention = f"<@&{community.alerts_role_id}>"
            else:
                alerts_role_mention = "None"

            if community.reasons_filter is None:
                reports_filter = "All"
            elif community.reasons_filter is None:
                reports_filter = "None"
            else:
                reports_filter = "\n- ".join(community.reasons_filter.to_list(custom_msg="Custom", with_emoji=True))

            embed = discord.Embed()
            embed.add_field(
                name="Reports feed",
                value=(
                    f"-# *{await get_command_mention(self.bot.tree, 'config', 'reports-channel')}*"
                    f"\n> -# The text channel where you receive new reports."
                    f"\n- {reports_channel_mention}"
                ),
                inline=True
            )
            embed.add_field(
                name="Confirmations feed",
                value=(
                    f"-# *{await get_command_mention(self.bot.tree, 'config', 'confirmations-channel')}*"
                    f"\n> -# The text channel where you receive report confirmations."
                    f"\n- {confirmations_channel_mention}"
                ),
                inline=True
            )
            embed.add_field(
                name="Alerts feed",
                value=(
                    f"-# *{await get_command_mention(self.bot.tree, 'config', 'alerts-channel')}*"
                    f"\n> -# The text channel where you receive player alerts."
                    f"\n- {alerts_channel_mention}"
                ),
                inline=True
            )
            embed.add_field(
                name="Reports filter",
                value=(
                    f"-# *{await get_command_mention(self.bot.tree, 'config', 'reports-filter')}*"
                    f"\n> -# Which categories of reports to receive."
                    f"\n- {reports_filter}"
                ),
                inline=True
            )
            embed.add_field(
                name="Admin role",
                value=(
                    f"-# *{await get_command_mention(self.bot.tree, 'config', 'admin-role')}*"
                    f"\n> -# The role that can review reports."
                    f"\n- {admin_role_mention}"
                ),
                inline=True
            )
            embed.add_field(
                name="Alerts role",
                value=(
                    f"-# *{await get_command_mention(self.bot.tree, 'config', 'alerts-role')}*"
                    f"\n> -# The role that gets notified for alerts."
                    f"\n- {alerts_role_mention}"
                ),
                inline=True
            )
            embed.add_field(
                name="Integrations",
                value=(
                    f"-# *{await get_command_mention(self.bot.tree, 'config', 'integrations')}*"
                    f"\n-# *Use the command above to see your integrations.*"
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
