import re
from discord import ButtonStyle, Interaction
from discord.ui import TextInput, Button
from bunker import schemas
from bunker.constants import DISCORD_ENROLL_CHANNEL_ID

from bunker.crud.communities import create_new_community, get_admin_by_id
from bunker.db import session_factory
from bunker.discord.utils import View, CallableButton, CustomException, format_url, get_command_mention, get_success_embed
from bunker.discord.views.community_overview import CommunityBaseModal, get_community_embed

RE_BATTLEMETRICS_URL = re.compile(r"https:\/\/(?:www\.)?battlemetrics\.com\/servers\/hll\/\d+")

class EnrollView(View):
    def __init__(self):
        super().__init__(timeout=None)

        self.add_item(CallableButton(
            self.send_owner_form,
            style=ButtonStyle.blurple,
            label="Request access",
            custom_id="enroll"
        ))

    async def send_owner_form(self, interaction: Interaction):
        async with session_factory() as db:
            admin = await get_admin_by_id(db, interaction.user.id)
            if admin:
                if admin.owned_community:
                    raise CustomException(
                        f"You are already registered as owner of {admin.community.name}!",
                        f"If you want to update your community details, use {await get_command_mention(interaction.client.tree, 'community', guild_only=True)}."
                    )
                elif admin.community:
                    raise CustomException(
                        f"You are already an admin for {admin.community.name}!",
                        (
                            f"Either resign using {await get_command_mention(interaction.client.tree, 'leave-community', guild_only=True)} or"
                            f" ask the existing owner to transfer ownership."
                        )
                    )
        
        modal = EnrollModal()
        await interaction.response.send_modal(modal)


class EnrollModal(CommunityBaseModal, title="Sign up your community"):
    battlemetrics_url = TextInput(
        label="Battlemetrics URL",
        placeholder='eg. "https://www.battlemetrics.com/servers/hll/12345"',
    )

    async def on_submit(self, interaction: Interaction):
        bm_url_match = RE_BATTLEMETRICS_URL.match(self.battlemetrics_url.value)
        if not bm_url_match:
            raise CustomException(
                "Invalid Battlemetrics URL!",
                "Please visit [Battlemetrics](https://www.battlemetrics.com/servers/hll), search for your server, click on it, and copy the URL."
            )

        channel = interaction.client.get_channel(DISCORD_ENROLL_CHANNEL_ID)
        if not channel:
            raise CustomException(
                "Could not send application!",
                "Channel not found. Reach out to an administrator."
            )
        
        params = schemas.CommunityCreateParams(
            name=self.name.value,
            tag=self.tag.value,
            contact_url=self.contact_url.value,
            owner_id=interaction.user.id,
            owner_name=interaction.user.display_name,
        )

        embed = get_community_embed(params)
        embed.set_author(
            name=params.owner_name,
            icon_url=interaction.user.avatar.url
        )
        embed.add_field(
            name="Server",
            value=format_url("View on Battlemetrics", bm_url_match.group()),
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
        content = interaction.message.embeds[0].fields[-1].value
        payload = content[8:-4] # Strip discord formatting
        params = schemas.CommunityCreateParams.model_validate_json(payload)
        
        async with session_factory() as db:
            await create_new_community(db, params)
        
        self.button.disabled = True
        self.button.label = "Accepted!"
        await interaction.response.edit_message(view=self)

