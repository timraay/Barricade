from functools import partial
import re
from discord import ButtonStyle, Color, Interaction
import discord
from discord.ui import TextInput
from pydantic import ValidationError
import pydantic_core
from barricade import schemas
from barricade.constants import DISCORD_ENROLL_CHANNEL_ID

from barricade.crud.communities import create_new_community, get_admin_by_id
from barricade.db import session_factory
from barricade.discord.utils import View, CallableButton, CustomException, format_url, get_command_mention, get_success_embed
from barricade.discord.views.community_overview import CommunityBaseModal
from barricade.enums import Emojis

RE_BATTLEMETRICS_URL = re.compile(r"^https:\/\/(?:www\.)?battlemetrics\.com\/servers\/hll\/\d+$")
RE_SERVER_ADDRESS = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d{3,5}$")

class EnrollView(View):
    def __init__(self):
        super().__init__(timeout=None)

        self.add_item(CallableButton(
            partial(self.send_owner_form, True),
            style=ButtonStyle.blurple,
            label="Request access (PC)",
            custom_id="enroll_pc"
        ))
        self.add_item(CallableButton(
            partial(self.send_owner_form, False),
            style=ButtonStyle.blurple,
            label="Request access (Console)",
            custom_id="enroll_console"
        ))

    async def send_owner_form(self, is_pc: bool, interaction: Interaction):
        async with session_factory() as db:
            admin = await get_admin_by_id(db, interaction.user.id)
            if admin and admin.community:
                if not admin.owned_community:
                    raise CustomException(
                        f"You are already an admin for {admin.community.name}!",
                        (
                            f"Either resign using {await get_command_mention(interaction.client.tree, 'leave-community', guild_only=True)} or" # type: ignore
                            f" ask the existing owner to transfer ownership."
                        )
                    )
                elif (is_pc and not admin.community.is_pc) or (not is_pc and not admin.community.is_console):
                    raise CustomException(
                        f"You are already registered as owner of {admin.community.name}!",
                        f"If you want to change what platform(s) your community hosts servers for, please reach out to Bunker staff."
                    )
                else:
                    raise CustomException(
                        f"You are already registered as owner of {admin.community.name}!",
                        f"If you want to update your community details, use {await get_command_mention(interaction.client.tree, 'community', guild_only=True)}." # type: ignore
                    )
        
        if is_pc:
            modal = PCEnrollModal()
        else:
            modal = ConsoleEnrollModal()
        await interaction.response.send_modal(modal)


class EnrollModal(CommunityBaseModal, title="Sign up your community"):
    def get_params(self, interaction: Interaction):
        return schemas.CommunityCreateParams(
            name=self.name.value,
            tag=self.tag.value,
            contact_url=self.contact_url.value,
            owner_id=interaction.user.id,
            owner_name=interaction.user.display_name,
            is_pc=False,
            is_console=False,
        )
    
    def get_server_value(self) -> str:
        return "Unknown"


    async def on_submit(self, interaction: Interaction):
        channel = interaction.client.get_channel(DISCORD_ENROLL_CHANNEL_ID)
        if not channel:
            raise CustomException(
                "Could not send application!",
                "Channel not found. Reach out to an administrator."
            )
        if not isinstance(channel, discord.TextChannel):
            raise CustomException(
                "Could not send application!",
                "Invalid channel configured. Reach out to an administrator."
            )
        
        params = self.get_params(interaction)
        
        embed = discord.Embed(
            title=f"{params.tag} {params.name}",
            color=Color.blurple(),
        )
        embed.add_field(
            name="Contact URL",
            value=f"{Emojis.CONTACT} {params.contact_url}",
            inline=True
        )
        embed.add_field(
            name=f"Owner",
            value=f"{interaction.user.display_name}\n{interaction.user.mention}",
            inline=True
        )
        embed.add_field(
            name="Server",
            value=self.get_server_value(),
            inline=True,
        )
        embed.add_field(
            name="Payload",
            value="```json\n" + params.model_dump_json(indent=2, exclude_unset=True) + "\n```",
            inline=False
        )

        await channel.send(embed=embed, view=EnrollAcceptView())
        await interaction.response.send_message(embed=get_success_embed(
            "Application sent!",
            "Your application was submitted for review. You will automatically receive your roles once accepted."
        ), ephemeral=True)


class PCEnrollModal(EnrollModal, title="[PC] Sign up your community"):
    battlemetrics_url = TextInput(
        label="Battlemetrics URL",
        placeholder='eg. "https://www.battlemetrics.com/servers/hll/12345"',
    )

    def get_params(self, interaction: Interaction):
        params = super().get_params(interaction)
        params.is_pc = True
        return params
    
    def get_server_value(self):
        return format_url("View on Battlemetrics", self.battlemetrics_url.value)

    async def on_submit(self, interaction: Interaction):
        bm_url_match = RE_BATTLEMETRICS_URL.match(self.battlemetrics_url.value)
        if not bm_url_match:
            raise CustomException(
                "Invalid Battlemetrics URL!",
                "Please visit [Battlemetrics](https://www.battlemetrics.com/servers/hll), search for your server, click on it, and copy the URL."
            )

        return await super().on_submit(interaction)

class ConsoleEnrollModal(EnrollModal, title="[Console] Sign up your community"):
    image_url = TextInput(
        label="URL to image of HLL server in browser",
        placeholder='eg. "https://imgur.com/i/..."',
    )

    def get_params(self, interaction: Interaction):
        params = super().get_params(interaction)
        params.is_console = True
        return params
    
    def get_server_value(self):
        return format_url("View Image", str(pydantic_core.Url(self.image_url.value)))

    async def on_submit(self, interaction: Interaction):
        invalid_url_exc = CustomException(
            "Invalid URL!",
            (
                "Please provide a valid URL to an image. The image must show your game server on your server management panel or in the server browser."
                " Your server's name needs to be visible. [Imgur](https://imgur.com/upload) is recommended to quickly upload your image online."
            )
        )
        try:
            url = pydantic_core.Url(self.image_url.value)
        except ValidationError:
            raise invalid_url_exc
        
        if not url.host:
            raise invalid_url_exc
        if "discord" in url.host and not (url.host.startswith("cdn.") or url.host.startswith("media")):
            raise invalid_url_exc

        return await super().on_submit(interaction)
        
class EnrollAcceptView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.button = CallableButton(
            self.accept_enrollment,
            label="Accept",
            style=ButtonStyle.green,
            custom_id="enroll_accept"
        )
        self.add_item(self.button)
    
    async def accept_enrollment(self, interaction: Interaction):
        content: str = interaction.message.embeds[0].fields[-1].value # type: ignore
        payload = content[8:-4] # Strip discord formatting
        params = schemas.CommunityCreateParams.model_validate_json(payload)
        
        async with session_factory.begin() as db:
            await create_new_community(db, params)
        
        self.button.disabled = True
        self.button.label = "Accepted!"
        await interaction.response.edit_message(view=self)

