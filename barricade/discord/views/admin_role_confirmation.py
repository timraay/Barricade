import asyncio
import discord
from discord import ButtonStyle, Interaction, Role

from barricade.crud.communities import get_admin_by_id
from barricade.db import session_factory
from barricade.discord.audit import audit_community_edit
from barricade.discord.utils import View, CallableButton, CustomException, get_question_embed, get_success_embed

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
            owner = await get_admin_by_id(db, interaction.user.id)
            if not owner or not owner.owned_community:
                raise CustomException(
                    "You need to be a community owner to do this!"
                )
            owner.community.admin_role_id = self.role.id

            await interaction.response.edit_message(embed=get_success_embed(
                title=f'Set "@{self.role.name}" as the new admin role for {owner.community.name}!'
            ), view=None)

            await owner.community.awaitable_attrs.owner
            asyncio.create_task(
                audit_community_edit(
                    community=owner.community,
                    by=interaction.user,
                )
            )
