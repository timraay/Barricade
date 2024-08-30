import discord
from discord import app_commands, Interaction
from discord.ext import commands
from barricade.constants import DISCORD_GUILD_ID, DISCORD_OWNER_ROLE_ID, MAX_ADMIN_LIMIT

from barricade.discord.bot import Bot
from barricade.discord.utils import get_command_mention, get_success_embed
from barricade.discord.views.enroll import EnrollView
from barricade.discord.views.submit_report import GetSubmissionURLView

@app_commands.guilds(DISCORD_GUILD_ID)
@app_commands.default_permissions(manage_guild=True)
class SetupCog(commands.GroupCog, group_name='setup'):
    def __init__(self, bot: Bot):
        self.bot = bot

    @app_commands.command(name="send-submission-start-message")
    async def create_submission_start_message(self, interaction: Interaction):
        await interaction.channel.send( # type: ignore
            content=(
                "## Submitting a report"
                "\nHad a player significantly disrupt your server? Then submit a report to Barricade!"
                " That way, your evidence gets shared with other community admins, who can then"
                " preemptively ban the player and prevent them from repeating their actions."
                "\n\n"
                "> Only severe violations should warrant getting someone banned across many community servers."
                "\n> As a rule of thumb, **only report players that do not deserve a second chance**."
                "\n_ _"
            ),
            embed=discord.Embed(title="Submit a report"),
            view=GetSubmissionURLView()
        )

        await interaction.response.send_message(
            embed=get_success_embed("Message sent!"),
            ephemeral=True
        )

    @app_commands.command(name="send-community-enroll-message")
    async def create_community_enroll_message(self, interaction: Interaction):
        await interaction.channel.send( # type: ignore
            content=(
                "### Are you the owner of a Hell Let Loose server?"
                "\nRequest to join the Bunker to claim the"
                f" <@&{DISCORD_OWNER_ROLE_ID}> role and get access to **server-related announcements**"
                " as well as **Barricade**, the community's collaborative ban sharing platform."
                "\n"
                "\n> Do not submit more than one request per community."
                f"\n> Once accepted, you can grant access to **{MAX_ADMIN_LIMIT}** additional admins"
                f" using the {await get_command_mention(self.bot.tree, 'add-admin', guild_only=True)}"
                " command."
                "\n"
                "\n**Note to console server owners:**"
                "\nTo verify your community owns a server we need a link to a picture with your **game server** being visible either on **your server management panel** or in the **in-game server browser**."
                "\nWe recommend you upload the picture to [Imgur](<https://imgur.com/upload>), but other image hosting platforms are fine too."
                "\n_ _"
            ),
            embed=discord.Embed(
                title="Request access to Bunker",
                description="-# Requests are manually reviewed. Please be patient."
            ),
            view=EnrollView(),
            allowed_mentions=discord.AllowedMentions.none()
        )

        await interaction.response.send_message(
            embed=get_success_embed("Message sent!"),
            ephemeral=True
        )


async def setup(bot: Bot):
    await bot.add_cog(SetupCog(bot))