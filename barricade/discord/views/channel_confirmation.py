import discord
from discord import ButtonStyle, Guild, Interaction, TextChannel
from sqlalchemy.ext.asyncio import AsyncSession

from barricade import schemas
from barricade.crud.communities import get_admin_by_id, get_community_by_id
from barricade.db import models, session_factory
from barricade.discord.audit import audit_community_edit
from barricade.discord.utils import View, CallableButton, CustomException, get_command_mention, get_danger_embed, get_question_embed, get_success_embed
from barricade.utils import safe_create_task

async def get_admin(db: AsyncSession, admin_id: int):
    # Make sure the user is part of a community
    db_admin = await get_admin_by_id(db, admin_id)
    if not db_admin or not db_admin.community:
        raise CustomException(
            "You need to be a community admin to do this!"
        )
    return db_admin

async def assert_community_guild(db_community: models.Community, interaction: Interaction):
    assert interaction.guild is not None
    if not db_community.forward_guild_id:
        db_community.forward_guild_id = interaction.guild.id
    elif db_community.forward_guild_id != interaction.guild.id:
        raise CustomException(
            "Your community was already (partially) configured in another Discord server!",
            (
                "If you want to move to this Discord server, use the"
                f" {await get_command_mention(interaction.client.tree, 'config', 'update-guild')} command." # type: ignore
            )
        )
    elif (
        db_community.admin_role_id
        and not discord.utils.get(
            interaction.guild.roles,
            id=db_community.admin_role_id
        )
    ):
        # If the admin role is no longer part of the updated guild, remove it
        db_community.admin_role_id = None

class UpdateGuildConfirmationView(View):
    def __init__(self, guild: Guild):
        super().__init__()
        self.guild = guild

        self.confirm_button = CallableButton(self.confirm, style=ButtonStyle.red, label="Confirm", single_use=True)
        self.add_item(self.confirm_button)

    async def send(self, interaction: Interaction):
        await interaction.response.send_message(embed=get_danger_embed(
            title=f'Do you want to move your config over to this server?',
            description=(
                'The following settings will be reset:'
                '\n'
                '\n- Reports feed'
                '\n- Confirmations feed'
                '\n- Alerts feed'
                '\n- Admin role'
                '\n- Alerts role'
            ),
        ), view=self, ephemeral=True)

    async def confirm(self, interaction: Interaction):
        async with session_factory.begin() as db:
            db_admin = await get_admin(db, interaction.user.id)
            assert db_admin.community is not None
            db_community = db_admin.community

            db_community.forward_guild_id = self.guild.id
            db_community.forward_channel_id = None
            db_community.confirmations_channel_id = None
            db_community.alerts_channel_id = None
            db_community.admin_role_id = None
            db_community.alerts_role_id = None
            await db.flush()

            await interaction.response.edit_message(embed=get_success_embed(
                title=f'Moved {db_admin.community.name} to the current server!'
            ), view=None)

            community_id = db_admin.community.id

            db.expunge(db_community)
            db_community = await get_community_by_id(db, community_id)
            community = schemas.Community.model_validate(db_community)

            safe_create_task(
                audit_community_edit(
                    community=community,
                    by=interaction.user, # type: ignore
                )
            )

class ReportChannelConfirmationView(View):
    def __init__(self, channel: TextChannel | None):
        super().__init__()
        self.channel = channel

        self.confirm_button = CallableButton(self.confirm, style=ButtonStyle.green, label="Confirm", single_use=True)
        self.add_item(self.confirm_button)

    async def send(self, interaction: Interaction):
        if self.channel:
            embed = get_question_embed(
                title=f'Do you want to set "#{self.channel.name}" as your new report feed?',
                description='This channel should only be visible to your admins.',
            )
        else:
            embed = get_question_embed(
                title=f'Do you want to remove your current report feed?',
                description='This will stop any new reports from coming in.',
            )

        await interaction.response.send_message(embed=embed, view=self, ephemeral=True)

    async def confirm(self, interaction: Interaction):
        async with session_factory.begin() as db:
            db_admin = await get_admin(db, interaction.user.id)
            assert db_admin.community is not None
            db_community = db_admin.community
            
            await assert_community_guild(db_community, interaction)
            db_community.forward_channel_id = self.channel.id if self.channel else None
            await db.flush()

            await interaction.response.edit_message(embed=get_success_embed(
                title=(
                    f'Set "#{self.channel.name}" as the new report feed for {db_admin.community.name}!'
                    if self.channel else
                    f'Removed the report feed for {db_admin.community.name}'
                )
            ), view=None)

            community_id = db_admin.community.id

            db.expunge(db_community)
            db_community = await get_community_by_id(db, community_id)
            community = schemas.Community.model_validate(db_community)

            safe_create_task(
                audit_community_edit(
                    community=community,
                    by=interaction.user, # type: ignore
                )
            )

class ConfirmationsChannelConfirmationView(View):
    def __init__(self, channel: TextChannel | None, default: bool = False):
        super().__init__()
        self.channel = channel
        self.default = default

        self.confirm_button = CallableButton(self.confirm, style=ButtonStyle.green, label="Confirm", single_use=True)
        self.add_item(self.confirm_button)

    async def send(self, interaction: Interaction):
        if self.channel:
            embed = get_question_embed(
                title=f'Do you want to set "#{self.channel.name}" as your new confirmations feed?',
                description='This channel should only be visible to your admins.',
            )
        elif self.default:
            embed = get_question_embed(
                title=f'Do you want to set your confirmations feed to match your reports feed?',
                description='Both will be sent to the same channel, assuming the reports feed is set up.',
            )
        else:
            embed = get_question_embed(
                title=f'Do you want to remove your current confirmations feed?',
                description='Future confirmations will be sent via DMs instead.',
            )

        await interaction.response.send_message(embed=embed, view=self, ephemeral=True)

    async def confirm(self, interaction: Interaction):
        async with session_factory.begin() as db:
            db_admin = await get_admin(db, interaction.user.id)
            assert db_admin.community is not None
            db_community = db_admin.community

            await assert_community_guild(db_community, interaction)
            db_community.confirmations_channel_id = (
                self.channel.id if self.channel else
                None if self.default else 0
            )
            await db.flush()

            await interaction.response.edit_message(embed=get_success_embed(
                title=(
                    f'Set "#{self.channel.name}" as the new confirmations feed for {db_admin.community.name}!'
                    if self.channel else
                    f'Set the confirmations feed for {db_admin.community.name} to match the reports feed!'
                    if self.default else
                    f'Removed the confirmations feed for {db_admin.community.name}'
                )
            ), view=None)

            community_id = db_community.id

            db.expunge(db_community)
            db_community = await get_community_by_id(db, community_id)
            community = schemas.Community.model_validate(db_community)

            safe_create_task(
                audit_community_edit(
                    community=community,
                    by=interaction.user, # type: ignore
                )
            )

class AlertsChannelConfirmationView(View):
    def __init__(self, channel: TextChannel | None, default: bool = False):
        super().__init__()
        self.channel = channel
        self.default = default

        self.confirm_button = CallableButton(self.confirm, style=ButtonStyle.green, label="Confirm", single_use=True)
        self.add_item(self.confirm_button)

    async def send(self, interaction: Interaction):
        if self.channel:
            embed = get_question_embed(
                title=f'Do you want to set "#{self.channel.name}" as your new alerts feed?',
                description='This channel should only be visible to your admins.',
            )
        elif self.default:
            embed = get_question_embed(
                title=f'Do you want to set your alerts feed to match your reports feed?',
                description='Both will be sent to the same channel, assuming the reports feed is set up.',
            )
        else:
            embed = get_question_embed(
                title=f'Do you want to remove your current alerts feed?',
                description='You will no longer receive alerts of reported players joining your servers.',
            )

        await interaction.response.send_message(embed=embed, view=self, ephemeral=True)

    async def confirm(self, interaction: Interaction):
        async with session_factory.begin() as db:
            db_admin = await get_admin(db, interaction.user.id)
            assert db_admin.community is not None
            db_community = db_admin.community

            await assert_community_guild(db_community, interaction)
            db_community.alerts_channel_id = (
                self.channel.id if self.channel else
                None if self.default else 0
            )
            await db.flush()

            await interaction.response.edit_message(embed=get_success_embed(
                title=(
                    f'Set "#{self.channel.name}" as the new alerts feed for {db_admin.community.name}!'
                    if self.channel else
                    f'Set the alerts feed for {db_admin.community.name} to match the reports feed!'
                    if self.default else
                    f'Removed the alerts feed for {db_admin.community.name}'
                )
            ), view=None)
            
            community_id = db_community.id

            db.expunge(db_community)
            db_community = await get_community_by_id(db, community_id)
            community = schemas.Community.model_validate(db_community)

            safe_create_task(
                audit_community_edit(
                    community=community,
                    by=interaction.user, # type: ignore
                )
            )
