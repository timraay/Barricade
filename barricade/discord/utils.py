from datetime import datetime, timedelta
import functools
import logging

from typing import Callable, Optional, Any, Awaitable

import discord
from discord import ui, app_commands, Interaction, ButtonStyle, Emoji, PartialEmoji, SelectOption
from discord.ext import commands
from discord.utils import escape_markdown as esc_md, MISSING

from barricade.constants import DISCORD_GUILD_ID
from barricade.utils import async_ttl_cache

class CallableButton(ui.Button):
    def __init__(self,
        callback: Callable[[Interaction], Awaitable[Any]],
        *args: Any,
        style: ButtonStyle = ButtonStyle.secondary,
        label: Optional[str] = None,
        disabled: bool = False,
        custom_id: Optional[str] = None,
        url: Optional[str] = None,
        emoji: Optional[str | Emoji | PartialEmoji] = None,
        row: Optional[int] = None,
        single_use: bool = False,
        **kwargs: Any
    ):
        super().__init__(
            style=style,
            label=label,
            disabled=disabled,
            custom_id=custom_id,
            url=url,
            emoji=emoji,
            row=row
        )
        self._callback = callback
        self._args = args
        self._kwargs = kwargs

        self.single_use = single_use
        self._has_been_used = False

    async def callback(self, interaction: Interaction):
        if self.single_use:
            if self._has_been_used:
                raise ExpiredButtonError
            await self._callback(interaction, *self._args, **self._kwargs)
            self._has_been_used = True
        
        else:
            await self._callback(interaction, *self._args, **self._kwargs)

class CallableSelect(ui.Select):
    def __init__(self,
        callback: Callable[[Interaction, list[str]], Awaitable[Any]],
        *args,
        custom_id: str = MISSING,
        placeholder: Optional[str] = None,
        min_values: int = 1,
        max_values: int = 1,
        options: list[SelectOption] = MISSING,
        disabled: bool = False,
        row: Optional[int] = None,
        **kwargs
    ):
        super().__init__(
            custom_id=custom_id,
            placeholder=placeholder,
            min_values=min_values,
            max_values=max_values,
            options=options,
            disabled=disabled,
            row=row
        )
        self._callback = callback
        self._args = args
        self._kwargs = kwargs

    async def callback(self, interaction: Interaction):
        await self._callback(interaction, self.values, *self._args, **self._kwargs)


def get_error_embed(title: str, description: str | None = None):
    embed = discord.Embed(color=discord.Color.from_rgb(221, 46, 68))
    embed.set_author(name=title, icon_url='https://cdn.discordapp.com/emojis/808045512393621585.png')
    if description:
        embed.description = description
    return embed

def get_success_embed(title: str, description: str | None = None):
    embed = discord.Embed(color=discord.Color(7844437))
    embed.set_author(name=title, icon_url="https://cdn.discordapp.com/emojis/809149148356018256.png")
    if description:
        embed.description = description
    return embed

def get_question_embed(title: str, description: str | None = None):
    embed = discord.Embed(color=discord.Color(3315710))
    embed.set_author(name=title, icon_url='https://cdn.discordapp.com/attachments/729998051288285256/924971834343059496/unknown.png')
    if description:
        embed.description = description
    return embed

def get_danger_embed(title: str, description: str | None = None):
    embed = discord.Embed(color=discord.Color(0xffcc4d))
    embed.set_author(name=title, icon_url='https://cdn.discordapp.com/attachments/695232527123742745/1188991491150991470/warning.png')
    if description:
        embed.description = description
    return embed


class ExpiredButtonError(Exception):
    """Raised when pressing a button that has already expired"""

class CustomException(Exception):
    """Raised to log a custom exception"""
    def __init__(self, error, *args, log_traceback: bool = False):
        self.error = error
        self.log_traceback = log_traceback
        super().__init__(*args)


def get_error_embed_from_exc(error: Exception):
    if isinstance(error, (app_commands.CommandInvokeError, commands.CommandInvokeError)):
        error = error.original

    if isinstance(error, (app_commands.CommandNotFound, commands.CommandNotFound)):
        embed = get_error_embed(title='Unknown command!')

    elif isinstance(error, CustomException):
        embed = get_error_embed(title=error.error, description=str(error))
        if error.log_traceback:
            logging.error("An unexpected error occured when handling an interaction", exc_info=error)
    
    elif isinstance(error, ExpiredButtonError):
        embed = get_error_embed("This action no longer is available.")
    elif isinstance(error, (app_commands.CommandOnCooldown, commands.CommandOnCooldown)):
        sec = timedelta(seconds=int(error.retry_after))
        d = datetime(1,1,1) + sec
        output = ("%dh%dm%ds" % (d.hour, d.minute, d.second))
        if output.startswith("0h"):
            output = output.replace("0h", "")
        if output.startswith("0m"):
            output = output.replace("0m", "")
        embed = get_error_embed(
            "That command is still on cooldown!",
            "Cooldown expires in " + output + "."
        )
    elif isinstance(error, (app_commands.MissingPermissions, commands.MissingPermissions)):
        embed = get_error_embed("Missing required permissions to use that command!", str(error))
    elif isinstance(error, (app_commands.BotMissingPermissions, commands.BotMissingPermissions)):
        embed = get_error_embed("I am missing required permissions to use that command!", str(error))
    elif isinstance(error, (app_commands.CheckFailure, commands.CheckFailure)):
        embed = get_error_embed("Couldn't run that command!")
    elif isinstance(error, commands.MissingRequiredArgument):
        embed = get_error_embed("Missing required argument(s)!", str(error))
    elif isinstance(error, commands.MaxConcurrencyReached):
        embed = get_error_embed("You can't do that right now!", str(error))
    elif isinstance(error, discord.NotFound):
        embed = get_error_embed("Could not find that channel or user!", str(error))
    elif isinstance(error, commands.BadArgument):
        embed = get_error_embed("Invalid argument!", esc_md(str(error)))
    else:
        embed = get_error_embed("An unexpected error occured!", esc_md(str(error)))
        logging.error("An unexpected error occured when handling an interaction", exc_info=error)
    
    return embed

async def handle_error(interaction: Interaction | commands.Context, error: Exception):
    embed = get_error_embed_from_exc(error)

    if isinstance(interaction, Interaction):
        if interaction.response.is_done() or interaction.is_expired():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await interaction.send(embed=embed)

def handle_error_wrap(func):
    @functools.wraps(func)
    async def wrapper(self, interaction, *args, **kwargs):
        try:
            return await func(self, interaction, *args, **kwargs)
        except Exception as e:
            await handle_error(interaction, e)
    return wrapper


class View(ui.View):
    async def on_error(self, interaction: Interaction, error: Exception, item, /) -> None:
        await handle_error(interaction, error)

class Modal(ui.Modal):
    async def on_error(self, interaction: Interaction, error: Exception, /) -> None:
        await handle_error(interaction, error)

@async_ttl_cache(size=100, seconds=60*60*24)
async def get_command_mention(tree: discord.app_commands.CommandTree, name: str, subcommands: str | None = None, guild_only: bool = False):
    if guild_only:
        commands = await tree.fetch_commands(guild=discord.Object(DISCORD_GUILD_ID))
    else:
        commands = await tree.fetch_commands()
    command = next(cmd for cmd in commands if cmd.name == name)
    if subcommands:
        return f"</{command.name} {subcommands}:{command.id}>"
    else:
        return f"</{command.name}:{command.id}>"

def format_url(text: str, url: str):
    return f"[**{text}** 🡥]({url})"
