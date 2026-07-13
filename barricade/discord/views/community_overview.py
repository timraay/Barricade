import discord
from discord import ButtonStyle, Color, Embed, HTTPException, Interaction, Member
from discord.ui import TextInput

from barricade import schemas
from barricade.constants import MAX_ADMIN_LIMIT
from barricade.crud.communities import edit_community, get_community_by_id
from barricade.db import session_factory
from barricade.discord import bot
from barricade.discord.utils import (
    CallableButton,
    CustomException,
    Modal,
    View,
    get_command_mention,
)
from barricade.enums import Emojis, GameFlag
from barricade.utils import validate_url


class CommunityOverviewView(View):
    def __init__(self, community: schemas.Community, user: Member):
        super().__init__(timeout=500)
        self.user = user
        self.set_community(community)

    def set_community(self, community: schemas.Community):
        self.community = community
        self.admin = next(
            (admin for admin in community.admins if admin.discord_id == self.user.id),
            None,
        )
        self.is_owner = community.owner_id == self.user.id
        self.is_admin = self.admin and not self.is_owner

        self.clear_items()
        if self.is_owner:
            self.add_item(
                CallableButton(
                    self.open_edit_modal, style=ButtonStyle.blurple, label="Edit"
                )
            )

    async def open_edit_modal(self, interaction: Interaction):
        async with session_factory() as db:
            db_community = await get_community_by_id(db, self.community.id)
            community = schemas.Community.model_validate(db_community)
            self.set_community(community)

            if self.community.owner_id != interaction.user.id:
                raise CustomException("You no longer own this community!")

        modal = CommunityEditModal(self)
        await interaction.response.send_modal(modal)

    async def submit_edit_modal(
        self, interaction: Interaction, modal: "CommunityEditModal"
    ):
        async with session_factory.begin() as db:
            db_community = await get_community_by_id(db, self.community.id)
            if not db_community:
                raise CustomException("You are no longer part of a community!")

            community = schemas.Community.model_validate(db_community)
            if community.owner_id != interaction.user.id:
                raise CustomException("You no longer own this community!")

            params = schemas.CommunityEditParams.model_validate(db_community)
            params.name = modal.get_name()
            params.tag = modal.get_tag()
            params.contact_url = modal.get_contact_url()

            await edit_community(db, db_community, params, by=interaction.user)  # type: ignore

        self.set_community(community)
        embed = await self.get_embed(interaction)

        await interaction.response.edit_message(embed=embed, view=self)

    async def get_embed(self, interaction: Interaction):
        embed = Embed(
            title=f"{self.community.tag} {self.community.name}".strip(),
            color=Color.blurple(),
        )

        if (
            (guild_id := self.community.guild_id)
            and (guild := interaction.client.get_guild(guild_id))
            and (icon := guild.icon)
        ):
            embed.set_thumbnail(url=icon.url)

        embed.add_field(
            name="Contact",
            value=f"{Emojis.CONTACT} {self.community.contact_url}",
        )

        admin_list = []
        for admin in self.community.admins:
            try:
                member = await bot.get_or_fetch_member(admin.discord_id)
                admin_list.append(member.mention)
            except HTTPException:
                admin_list.append(admin.name)

            if self.community.owner_id == admin.discord_id:
                admin_list[-1] += f" {Emojis.OWNER}"

        if admin_list:
            embed.add_field(
                name=f"Admins ({len(self.community.admins)}/{MAX_ADMIN_LIMIT + 1})",
                value="\n".join(admin_list),
            )
        else:
            embed.color = Color.default()
            embed.description = "> This community was abandoned!"

        if self.community.games_bitflag == 0:
            games = "None"
        elif self.community.games_bitflag == GameFlag.HLL:
            games = "HLL"
        elif self.community.games_bitflag == GameFlag.HLLV:
            games = "HLL:V"
        else:
            games = "HLL & HLL:V"

        embed.add_field(
            name="Games",
            value=games,
        )

        if self.is_admin:
            embed.add_field(
                name="> Available commands (Admin)",
                value=(
                    ">>> -# "
                    + await get_command_mention(
                        interaction.client.tree,  # type: ignore
                        "leave-community",
                        guild_only=True,
                    )
                    + " - Leave this community"
                ),
                inline=False,
            )
        elif self.is_owner:
            embed.add_field(
                name="> Available commands (Owner)",
                value=(
                    ">>> -# "
                    + await get_command_mention(
                        interaction.client.tree,  # type: ignore
                        "add-admin",
                        guild_only=True,
                    )
                    + " - Add an admin to your community\n-# "
                    + await get_command_mention(
                        interaction.client.tree,  # type: ignore
                        "remove-admin",
                        guild_only=True,
                    )
                    + " - Remove an admin from your community\n-# "
                    + await get_command_mention(
                        interaction.client.tree,  # type: ignore
                        "transfer-ownership",
                        guild_only=True,
                    )
                    + " - Transfer ownership to one of your admins"
                ),
                inline=False,
            )

        return embed

    async def send(self, interaction: Interaction):
        embed = await self.get_embed(interaction)

        await interaction.response.send_message(embed=embed, view=self, ephemeral=True)


class CommunityBaseModal(Modal):
    # Also used by EnrollModal

    def __init__(self):
        super().__init__()

        self.name_input = TextInput(
            placeholder='eg. "My Community"',
            min_length=3,
            max_length=32,
        )

        self.tag_input = TextInput(
            required=False,
            placeholder='eg. "[ABC]", "DEF |"',
            min_length=2,
            max_length=8,
        )

        self.contact_url_input = TextInput(
            placeholder='eg. "discord.gg/ABC"',
            min_length=8,
            max_length=64,
        )

        self.add_item(
            discord.ui.Label(
                text="Community Name",
                description="The name of your community.",
                component=self.name_input,
            )
        )

        self.add_item(
            discord.ui.Label(
                text="Clan Tag",
                description="(Optional) The clan tag used by members of your community.",
                component=self.tag_input,
            )
        )

        self.add_item(
            discord.ui.Label(
                text="Contact URL",
                description="A permanent(!) link to your Discord or website that players can visit to contact you.",
                component=self.contact_url_input,
            )
        )

    def get_name(self) -> str:
        return self.name_input.value.strip()

    def get_tag(self) -> str:
        return self.tag_input.value.strip()

    def get_contact_url(self) -> str:
        contact_url = self.contact_url_input.value.strip()
        try:
            validate_url(contact_url)
        except ValueError as e:
            raise CustomException(
                "Invalid URL!",
                (
                    "The provided URL used to contact your community is invalid:"
                    "\n\n"
                    f"> {str(e)}"
                    "\n\n"
                    "Please try again with a different URL."
                ),
            ) from None
        return contact_url


class CommunityEditModal(CommunityBaseModal, title="Update Community"):
    def __init__(self, view: "CommunityOverviewView"):

        super().__init__()
        self.view = view

        community = view.community
        self.name_input.default = community.name
        self.tag_input.default = community.tag
        self.contact_url_input.default = community.contact_url

    async def on_submit(self, interaction: Interaction):
        await self.view.submit_edit_modal(interaction, self)
