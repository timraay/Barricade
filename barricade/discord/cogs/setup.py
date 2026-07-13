import ast
import contextlib

import discord
from discord import Interaction, app_commands
from discord.ext import commands

from barricade.constants import DISCORD_GUILD_ID, DISCORD_OWNER_ROLE_ID, MAX_ADMIN_LIMIT
from barricade.discord.bot import Bot
from barricade.discord.utils import (
    get_command_mention,
    get_success_embed,
    handle_error_wrap,
)
from barricade.discord.views.enroll import EnrollView
from barricade.discord.views.report_submission_start import ReportSubmissionStartView


def insert_returns(body):
    # insert return stmt if the l expression is a expression statement
    if isinstance(body[-1], ast.Expr):
        body[-1] = ast.Return(body[-1].value)
        ast.fix_missing_locations(body[-1])

    # for if statements, we insert returns into the body and the orelse
    if isinstance(body[-1], ast.If):
        insert_returns(body[-1].body)
        insert_returns(body[-1].orelse)

    # for with blocks, again we insert returns into the body
    if isinstance(body[-1], ast.With):
        insert_returns(body[-1].body)


@app_commands.guilds(DISCORD_GUILD_ID)
@app_commands.default_permissions(manage_guild=True)
class SetupCog(commands.GroupCog, group_name="setup"):
    def __init__(self, bot: Bot):
        self.bot = bot

    @app_commands.command(name="send-submission-start-message")
    async def create_submission_start_message(self, interaction: Interaction):
        assert isinstance(interaction.channel, discord.abc.Messageable)

        await interaction.channel.send(
            "## Submitting a report"
            "\nHad a player significantly disrupt your server? Then submit a report to Barricade!"
            "\nYour evidence will be shared with other community admins, allowing them to"
            " preemptively ban the player and prevent them from repeating their actions elsewhere."
            "\n\n"
            "> Only severe violations should warrant getting someone banned across many community servers."
            "\n> As a rule of thumb, **only report players that do not deserve a second chance**."
            "\n_ _"
        )

        await interaction.channel.send(
            view=ReportSubmissionStartView(),
        )

        await interaction.response.send_message(
            embed=get_success_embed("Message sent!"), ephemeral=True
        )

    @app_commands.command(name="send-community-enroll-message")
    async def create_community_enroll_message(self, interaction: Interaction):
        assert isinstance(interaction.channel, discord.abc.Messageable)

        content = (
            "### Are you the owner of a Hell Let Loose server?"
            f"\nRequest to join the Bunker to claim the <@&{DISCORD_OWNER_ROLE_ID}> role,"
            " granting you access to **server-related announcements**\nas well as **Barricade**,"
            " the community's collaborative ban sharing platform."
            "\n"
            "\n> Do not submit more than one request per community."
            f"\n> Once accepted, you can grant access to **{MAX_ADMIN_LIMIT}** additional admins"
            f" using the {await get_command_mention(self.bot.tree, 'add-admin', guild_only=True)} command."
            "\n_ _"
        )

        await interaction.channel.send(content)
        await interaction.channel.send(view=EnrollView())

        await interaction.response.send_message(
            embed=get_success_embed("Messages sent!"), ephemeral=True
        )

    @commands.command(
        description="Evaluate a python variable or expression",
        usage="r!eval <cmd>",
        hidden=True,
    )
    @commands.is_owner()
    @handle_error_wrap
    async def eval(self, ctx, *, cmd):
        fn_name = "_eval_expr"

        cmd = cmd.strip("` ")
        if cmd.startswith("py"):
            cmd = cmd.replace("py", "", 1)

        # add a layer of indentation
        cmd = "\n".join(f"    {i}" for i in cmd.splitlines())

        # wrap in async def body
        body = f"async def {fn_name}():\n{cmd}"

        parsed = ast.parse(body)
        body = parsed.body[0].body  # type: ignore

        insert_returns(body)

        env = {
            "bot": self.bot,
            "discord": discord,
            "commands": commands,
            "ctx": ctx,
            "__import__": __import__,
        }
        exec(compile(parsed, filename="<ast>", mode="exec"), env)

        result = await eval(f"{fn_name}()", env)
        with contextlib.suppress(discord.HTTPException):
            await ctx.send(result)


async def setup(bot: Bot):
    await bot.add_cog(SetupCog(bot))
