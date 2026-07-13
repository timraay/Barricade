import re
from functools import partial

import discord
from discord import ButtonStyle, Color, Interaction
from discord.ui import TextInput
from pydantic import ValidationError

from barricade import schemas
from barricade.crud.communities import create_new_community, get_admin_by_id
from barricade.db import session_factory
from barricade.discord.communities import get_enroll_channel
from barricade.discord.utils import (
    CallableButton,
    CustomException,
    LayoutView,
    Modal,
    View,
    get_command_mention,
    get_success_embed,
)
from barricade.discord.views.community_overview import CommunityBaseModal
from barricade.enums import Emojis, Game, GameFlag

RE_BATTLEMETRICS_URL = re.compile(
    r"^https:\/\/(?:www\.)?battlemetrics\.com\/servers\/hll\/\d+$"
)
RE_SERVER_ADDRESS = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d{3,5}$")


class EnrollView(LayoutView):
    def __init__(self):
        super().__init__(timeout=None)

        container = discord.ui.Container()
        container.add_item(
            discord.ui.TextDisplay(
                "**Request access to Bunker**"
                "\nSelect the game that you currently operate servers for. This can be changed later."
            )
        )
        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.Section(
                discord.ui.TextDisplay("🌲 **Hell Let Loose** (2021)"),
                accessory=CallableButton(
                    partial(self.send_owner_form, games_bitflag=Game.HLL.to_flag()),
                    style=ButtonStyle.blurple,
                    label="Request",
                    custom_id="enroll:hll",
                ),
            )
        )
        container.add_item(
            discord.ui.Section(
                discord.ui.TextDisplay("🌴 **Hell Let Loose: Vietnam** (2026)"),
                accessory=CallableButton(
                    partial(self.send_owner_form, games_bitflag=Game.HLLV.to_flag()),
                    style=ButtonStyle.blurple,
                    label="Request",
                    custom_id="enroll:hllv",
                ),
            )
        )
        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.Section(
                discord.ui.TextDisplay("▫️ Hosting servers for both games?"),
                accessory=CallableButton(
                    partial(self.send_owner_form, games_bitflag=GameFlag.all()),
                    style=ButtonStyle.gray,
                    label="Request",
                    custom_id="enroll:all",
                ),
            )
        )
        container.add_item(
            discord.ui.TextDisplay(
                "-# Requests are manually reviewed. Please allow up to 3 days."
            )
        )
        self.add_item(container)

    async def send_owner_form(self, interaction: Interaction, games_bitflag: GameFlag):
        async with session_factory() as db:
            admin = await get_admin_by_id(db, interaction.user.id)
            if admin and admin.community:
                if not admin.owned_community:
                    raise CustomException(
                        f"You are already an admin for {admin.community.name}!",
                        (
                            f"Either resign using {await get_command_mention(interaction.client.tree, 'leave-community', guild_only=True)} or"  # type: ignore
                            f" ask the existing owner to transfer ownership."
                        ),
                    )

                games_overlap = admin.community.games_bitflag & games_bitflag
                if (games_bitflag - games_overlap) != 0:
                    raise CustomException(
                        f"You are already registered as owner of {admin.community.name}!",
                        f"If you want to change what games your community hosts servers for, use {await get_command_mention(interaction.client.tree, 'config')}.",  # type: ignore
                    )

                raise CustomException(
                    f"You are already registered as owner of {admin.community.name}!",
                    f"If you want to update your community details, use {await get_command_mention(interaction.client.tree, 'community', guild_only=True)}.",  # type: ignore
                )

        modal = EnrollModal(games_bitflag=games_bitflag)
        await interaction.response.send_modal(modal)


class EnrollModal(CommunityBaseModal, title="Sign up your community"):
    def __init__(self, games_bitflag: GameFlag):
        super().__init__()
        self.games_bitflag = games_bitflag

        self.evidence_input = discord.ui.FileUpload(
            min_values=1,
            max_values=1,
        )

        self.add_item(
            discord.ui.TextDisplay(
                "You need to show that your community owns a server.\n"
                "-# Please **provide a screenshot** containing either of the following:\n"
                "> -# - The control panel of your server\n"
                "> -# - The server browser, with your server visible.\n"
                "-# Images only. Any other file types will be rejected."
            )
        )

        self.add_item(
            discord.ui.Label(
                text="Evidence of server ownership",
                component=self.evidence_input,
            )
        )

    def get_evidence_url(self) -> str:
        if not self.evidence_input.values:
            raise CustomException("At least one attachment must be provided!")
        file = self.evidence_input.values[0]
        if not file.content_type or not file.content_type.startswith("image/"):
            raise CustomException("Only images are allowed!")
        return file.url

    def get_params(self, interaction: Interaction):
        return schemas.CommunityCreateParams(
            name=self.get_name(),
            tag="",
            contact_url=self.get_contact_url(),
            owner_id=interaction.user.id,
            owner_name=interaction.user.display_name,
            games_bitflag=self.games_bitflag,
        )

    async def on_submit(self, interaction: Interaction):
        channel = get_enroll_channel()
        params = self.get_params(interaction)

        embed = discord.Embed(
            title=f"{params.tag} {params.name}".strip(),
            color=Color.blurple(),
        )
        embed.add_field(
            name="Contact URL",
            value=f"{Emojis.CONTACT} {params.contact_url}",
            inline=True,
        )
        embed.add_field(
            name="Owner",
            value=f"{interaction.user.display_name}\n{interaction.user.mention}",
            inline=True,
        )
        embed.add_field(
            name="Payload",
            value="```json\n"
            + params.model_dump_json(indent=2, exclude_unset=True)
            + "\n```",
            inline=False,
        )
        embed.set_image(url=self.get_evidence_url())

        await channel.send(embed=embed, view=EnrollAcceptView())

        # TODO: Warn user if they have DMs disabled (to receive rejection reason)
        await interaction.response.send_message(
            embed=get_success_embed(
                "Application sent!",
                (
                    "Your application was submitted for review."
                    " This may take up to 3 days."
                    " You will automatically receive your roles once accepted."
                ),
            ),
            ephemeral=True,
        )


class EnrollEditModal(Modal, title="Edit Application"):
    def __init__(self, params: schemas.CommunityCreateParams):
        super().__init__()
        self.input = TextInput(
            label="Parameters",
            style=discord.TextStyle.paragraph,
            default=params.model_dump_json(indent=2),
        )
        self.add_item(self.input)

    async def on_submit(self, interaction: Interaction):
        try:
            params = schemas.CommunityCreateParams.model_validate_json(self.input.value)
        except ValidationError as e:
            raise CustomException("Invalid parameters!", str(e)) from None

        await interaction.response.defer()
        message = await interaction.original_response()

        embed = message.embeds[0]
        embed._fields[-1]["value"] = (
            "```json\n" + params.model_dump_json(indent=2, exclude_unset=True) + "\n```"
        )  # type: ignore
        await interaction.edit_original_response(embed=embed)


class MessageApplicationModal(Modal):
    def __init__(self, member: discord.Member):
        super().__init__(title=f"Messaging {member.display_name}...")
        self.member = member
        self.input = TextInput(
            label="Message",
            style=discord.TextStyle.paragraph,
        )
        self.add_item(self.input)

    async def on_submit(self, interaction: Interaction):
        embed = discord.Embed(description=">>> " + self.input.value)
        embed.set_author(name="Message from Barricade staff:")
        embed.set_footer(
            text="You cannot reply to this message. Ask questions in the Discord server."
        )
        await self.member.send(embed=embed)
        await interaction.response.send_message(
            embed=get_success_embed("Message sent!"),
            ephemeral=True,
        )


class EnrollAcceptView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.accept_button = CallableButton(
            self.accept_enrollment,
            label="Accept",
            style=ButtonStyle.green,
            custom_id="enroll_accept",
        )
        self.deny_button = CallableButton(
            self.deny_enrollment,
            label="Deny",
            style=ButtonStyle.red,
            custom_id="enroll_deny",
        )
        self.edit_button = CallableButton(
            self.edit_enrollment,
            label="Edit",
            style=ButtonStyle.gray,
            custom_id="enroll_edit",
        )
        self.message_button = CallableButton(
            self.message_applicant,
            label="Message",
            style=ButtonStyle.blurple,
            custom_id="enroll_message_user",
        )
        self.add_item(self.accept_button)
        self.add_item(self.deny_button)
        self.add_item(self.edit_button)
        self.add_item(self.message_button)

    def get_params(self, interaction: Interaction) -> schemas.CommunityCreateParams:
        content: str = interaction.message.embeds[0].fields[-1].value  # type: ignore
        payload = content[8:-4]  # Strip discord formatting
        return schemas.CommunityCreateParams.model_validate_json(payload)

    async def accept_enrollment(self, interaction: Interaction):
        params = self.get_params(interaction)

        async with session_factory.begin() as db:
            await create_new_community(db, params)

        self.accept_button.disabled = True
        self.deny_button.disabled = True
        self.edit_button.disabled = True
        self.accept_button.label = "Accepted!"
        await interaction.response.edit_message(view=self)

    async def deny_enrollment(self, interaction: Interaction):
        self.accept_button.disabled = True
        self.deny_button.disabled = True
        self.edit_button.disabled = True
        self.deny_button.label = "Denied!"
        await interaction.response.edit_message(view=self)

    async def edit_enrollment(self, interaction: Interaction):
        params = self.get_params(interaction)
        modal = EnrollEditModal(params)
        await interaction.response.send_modal(modal)

    async def message_applicant(self, interaction: Interaction):
        params = self.get_params(interaction)
        assert interaction.guild is not None
        member = await interaction.guild.fetch_member(params.owner_id)

        modal = MessageApplicationModal(member)
        await interaction.response.send_modal(modal)
