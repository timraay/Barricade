from discord import ButtonStyle, Interaction, Role

from barricade import schemas
from barricade.crud.communities import get_community_by_id
from barricade.db import session_factory
from barricade.discord.audit import audit_community_edit
from barricade.discord.utils import View, CallableButton, get_question_embed, get_success_embed
from barricade.utils import safe_create_task

from .channel_confirmation import assert_community_guild, get_admin

class AdminRoleConfirmationView(View):
    def __init__(self, role: Role):
        super().__init__()
        self.role = role

        self.confirm_button = CallableButton(self.confirm, style=ButtonStyle.green, label="Confirm", single_use=True)
        self.add_item(self.confirm_button)

    async def send(self, interaction: Interaction):
        await interaction.response.send_message(embed=get_question_embed(
            title=f'Do you want to set "@{self.role.name}" as your new admin role?',
            description='Admins can review reports and may be notified if an unreviewed player joins a server.',
        ), view=self, ephemeral=True)

    async def confirm(self, interaction: Interaction):
        async with session_factory.begin() as db:
            db_admin = await get_admin(db, interaction.user.id)
            
            await assert_community_guild(db_admin.community, interaction)
            db_admin.community.admin_role_id = self.role.id
            await db.flush()

            await interaction.response.edit_message(embed=get_success_embed(
                title=f'Set "@{self.role.name}" as the new admin role for {db_admin.community.name}!'
            ), view=None)

            community_id = db_admin.community.id

            db.expunge(db_admin.community)
            db_community = await get_community_by_id(db, community_id)
            community = schemas.Community.model_validate(db_community)

            safe_create_task(
                audit_community_edit(
                    community=community,
                    by=interaction.user, # type: ignore
                )
            )

class AlertsRoleConfirmationView(View):
    def __init__(self, role: Role | None, default: bool = False):
        super().__init__()
        self.role = role
        self.default = False

        self.confirm_button = CallableButton(self.confirm, style=ButtonStyle.green, label="Confirm", single_use=True)
        self.add_item(self.confirm_button)

    async def send(self, interaction: Interaction):
        if self.role:
            embed = get_question_embed(
                title=f'Do you want to set "@{self.role.name}" as your new alerts role?',
                description='This is the role that will be notified when a player alert is received.',
            )
        elif self.default:
            embed = get_question_embed(
                title=f'Do you want to set your alerts role to match your admin role?',
                description='Alerted admins will be able to immediately ban the player, assuming the alerts role is set up.',
            )
        else:
            embed = get_question_embed(
                title=f'Do you want to remove your current alerts role?',
                description='No role will be mentioned when an alert is received.',
            )
        await interaction.response.send_message(embed=embed, view=self, ephemeral=True)

    async def confirm(self, interaction: Interaction):
        async with session_factory.begin() as db:
            db_admin = await get_admin(db, interaction.user.id)

            await assert_community_guild(db_admin.community, interaction)
            db_admin.community.alerts_role_id = (
                self.role.id if self.role else
                None if self.default else 0
            )
            await db.flush()

            await interaction.response.edit_message(embed=get_success_embed(
                title=(
                    f'Set "@{self.role.name}" as the new alerts role for {db_admin.community.name}!'
                    if self.role else
                    f'Set the alerts role for {db_admin.community.name} to match the admin role!'
                    if self.default else
                    f'Removed the alerts role for {db_admin.community.name}'
                )
            ), view=None)

            community_id = db_admin.community.id

            db.expunge(db_admin.community)
            db_community = await get_community_by_id(db, community_id)
            community = schemas.Community.model_validate(db_community)

            safe_create_task(
                audit_community_edit(
                    community=community,
                    by=interaction.user, # type: ignore
                )
            )
