from discord import ButtonStyle, Interaction, Role

from barricade import schemas
from barricade.crud.communities import get_admin_by_id, get_community_by_id
from barricade.db import session_factory
from barricade.discord.audit import audit_community_edit
from barricade.discord.utils import View, CallableButton, CustomException, get_question_embed, get_success_embed
from barricade.utils import safe_create_task

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
            db_admin = await get_admin_by_id(db, interaction.user.id)
            if not db_admin or not db_admin.community:
                raise CustomException(
                    "You need to be a community admin to do this!"
                )
            db_admin.community.admin_role_id = self.role.id
            await db.flush()

            await interaction.response.edit_message(embed=get_success_embed(
                title=f'Set "@{self.role.name}" as the new admin role for {db_admin.community.name}!'
            ), view=None)

            community_id = db_admin.community.id

            db.expire_all()
            db_community = await get_community_by_id(db, community_id)
            community = schemas.Community.model_validate(db_community)

            safe_create_task(
                audit_community_edit(
                    community=community,
                    by=interaction.user, # type: ignore
                )
            )
