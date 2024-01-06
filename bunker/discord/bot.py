import asyncio
import logging
import os
from pathlib import Path

import discord
from discord.ext import commands
from discord.utils import escape_markdown as esc_md

from bunker import schemas
from bunker.db import models
from bunker.discord.views.player_review import PlayerReviewView
from bunker.discord.utils import handle_error
from bunker.constants import DISCORD_COGS_PATH, DISCORD_GUILD_ID, DISCORD_ADMIN_ROLE_ID, DISCORD_OWNER_ROLE_ID, DISCORD_REPORTS_CHANNEL_ID
from bunker.utils import get_player_id_type, PlayerIDType

__all__ = (
    "bot",
)

async def load_all_cogs():
    cog_path_template = DISCORD_COGS_PATH.as_posix().replace("/", ".") + ".{}"
    for cog_name in os.listdir(DISCORD_COGS_PATH):
        if cog_name.endswith(".py"):
            try:
                cog_path = cog_path_template.format(os.path.splitext(cog_name)[0])
                await bot.load_extension(cog_path)
            except:
                logging.exception(f"Cog {cog_name} cannot be loaded")
                pass
    logging.info('Loaded all cogs')

async def sync_commands():
    try:
        await asyncio.wait_for(bot.tree.sync(guild=discord.Object(DISCORD_GUILD_ID)), timeout=5)
        await asyncio.wait_for(bot.tree.sync(), timeout=5)
        logging.info('Synced app commands')
    except asyncio.TimeoutError:
        logging.warn("Didn't sync app commands. This was likely last done recently, resulting in rate limits.")


class Bot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.remove_command('help')
        self.allowed_mentions = discord.AllowedMentions.none()
    
    async def setup_hook(self) -> None:
        await load_all_cogs()
        await sync_commands()
        # TODO: this is lazy and ugly
        from bunker.discord.views.submit_report import GetSubmissionURLView
        self.add_view(GetSubmissionURLView())

    @property
    def primary_guild(self):
        guild = self.get_guild(DISCORD_GUILD_ID)
        if guild is None:
            raise RuntimeError("Guild not found")
        return guild
    
    async def get_or_fetch_user(self, user_id: int):
        guild = self.primary_guild
        member = guild.get_member(user_id)
        if member:
            return member
        else:
            return await guild.fetch_member(user_id)
    
    def get_admin_roles(self):
        admin_role = self.primary_guild.get_role(DISCORD_ADMIN_ROLE_ID)
        if not admin_role:
            raise RuntimeError("Admin role not found")
        owner_role = self.primary_guild.get_role(DISCORD_OWNER_ROLE_ID)
        if not owner_role:
            raise RuntimeError("Owner role not found")
        return admin_role, owner_role
    
    def get_report_channel(self):
        return self.primary_guild.get_channel(DISCORD_REPORTS_CHANNEL_ID)

    async def grant_admin_role(self, user_id: int):
        admin_role, owner_role = self.get_admin_roles()
        user = await self.get_or_fetch_user(user_id)
        await user.add_roles(admin_role)
        await user.remove_roles(owner_role)

    async def grant_owner_role(self, user_id: int):
        admin_role, owner_role = self.get_admin_roles()
        user = await self.get_or_fetch_user(user_id)
        await user.add_roles(owner_role)
        await user.remove_roles(admin_role)

    async def revoke_admin_roles(self, user_id: int):
        admin_role, owner_role = self.get_admin_roles()
        user = await self.get_or_fetch_user(user_id)
        await user.remove_roles(admin_role, owner_role)

    async def get_report_message_payload(self, report: schemas.ReportCreateParams):
        content = ""
        for player in report.players:
            links = []
            content += f"**Username:** {esc_md(player.name)}\n"

            player_id_type = get_player_id_type(player.id)
            if player_id_type == PlayerIDType.STEAM_64_ID:
                content += f"**Steam ID:** `{player.id}`\n"
                links.append(f"[Steam profile](https://steamcommunity.com/profiles/{player.id})")
            elif player_id_type == PlayerIDType.UUID:
                content += f"**UUID:** {player.id}\n"
            
            if player.bm_rcon_url:
                links.append(f"[Battlemetrics profile]({player.bm_rcon_url})")
            if links:
                content += f"**Links:** " + " | ".join(links) + "\n"
            content += "\n"
        
        content += (
            "**Reason:** " + ", ".join(report.reasons)
            + "\n>>> " + esc_md(report.body)
        )

        embed = discord.Embed()
        try:
            user = await self.get_or_fetch_user(report.token.admin_id)
            embed.set_author(name=f"Report by {user.nick or user.display_name}", icon_url=user.avatar.url)
        except:
            embed.set_author(name=f"Report by {report.token.admin.name}")
        embed.set_footer(text=f"Community: {report.token.community.name} • {report.token.community.contact_url}")

        return schemas.DiscordMessagePayload(
            content=content,
            embeds=[embed]
        )

    async def send_report(self, payload: schemas.DiscordMessagePayload):
        channel = self.get_report_channel()
        message = await channel.send(**payload.model_dump())
        return message

    async def forward_report_to_community(self,
            report: models.Report,
            community: schemas.Community,
            payload: schemas.DiscordMessagePayload
    ):
        guild = self.get_guild(community.forward_guild_id)
        if not guild:
            return
        channel = guild.get_channel(community.forward_channel_id)
        if not channel:
            return
        
        responses = [schemas.PendingResponse(
            player_report=player,
            community=community
        ) for player in report.players]

        view = PlayerReviewView(responses=responses)
        await channel.send(**payload.model_dump(exclude_none=True), view=view)

def command_prefix(bot: Bot, message: discord.Message):
    return bot.user.mention + " "

bot = Bot(
    intents=discord.Intents.default(),
    command_prefix=command_prefix,
    case_insensitive=True
)

@bot.tree.error
async def on_interaction_error(interaction: discord.Interaction, error: Exception):
    await handle_error(interaction, error)

@bot.command()
@commands.is_owner()
async def reload(ctx: commands.Context, cog_name: str = None):
    async def reload_cog(ctx: commands.Context, cog_name):
        try:
            await bot.reload_extension(f"cogs.{cog_name}")
            await ctx.send(f"Reloaded {cog_name}")
        except Exception as e:
            await ctx.send(f"Couldn't reload {cog_name}, " + str(e))

    if not cog_name:
        for cog_name in os.listdir(Path("./cogs")):
            if cog_name.endswith(".py"):
                cog_name = cog_name.replace(".py", "")
                await reload_cog(ctx, cog_name)
    else:
        if os.path.exists(Path(f"./cogs/{cog_name}.py")):
            await reload_cog(ctx, cog_name)
        else:
            await ctx.send(f"{cog_name} doesn't exist")

