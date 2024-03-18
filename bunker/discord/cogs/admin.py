import discord
from discord import app_commands, Interaction
from discord.ext import commands
from bunker.constants import DISCORD_GUILD_ID

from bunker.discord.bot import Bot
from bunker.discord.utils import get_success_embed
from bunker.discord.views.enroll import EnrollView
from bunker.discord.views.submit_report import GetSubmissionURLView

@app_commands.guilds(DISCORD_GUILD_ID)
@app_commands.default_permissions(manage_messages=True)
class AdminCog(commands.GroupCog, group_name='admin'):
    def __init__(self, bot: Bot):
        self.bot = bot

    @app_commands.command(name="send-submission-start-message")
    async def create_submission_start_message(self, interaction: Interaction):
        await interaction.channel.send(
            embed=discord.Embed(title="Submit a report"),
            view=GetSubmissionURLView()
        )

        await interaction.response.send_message(
            embed=get_success_embed("Message sent!"),
            ephemeral=True
        )

    @app_commands.command(name="send-community-enroll-message")
    async def create_community_enroll_message(self, interaction: Interaction):
        await interaction.channel.send(
            embed=discord.Embed(title="Request access to Bunker"),
            view=EnrollView()
        )

        await interaction.response.send_message(
            embed=get_success_embed("Message sent!"),
            ephemeral=True
        )


async def setup(bot: Bot):
    await bot.add_cog(AdminCog(bot))