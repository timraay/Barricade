import discord

from barricade import schemas
from barricade.crud.communities import get_admin_by_id
from barricade.crud.reports import create_report, create_token
from barricade.db import session_factory
from barricade.discord.utils import (
    CustomException,
    get_success_container,
)
from barricade.discord.views.report_edit import ReportEditTagsModal, _ReportEditView


class ReportCreateView(_ReportEditView):
    def __init__(self):
        super().__init__()

    @classmethod
    async def new(cls, interaction: discord.Interaction) -> None:
        self = cls()
        modal = ReportEditTagsModal(self, send_on_submit=True)
        await interaction.response.send_modal(modal)

    async def submit_report(self, interaction: discord.Interaction) -> None:
        async with session_factory.begin() as db:
            db_admin = await get_admin_by_id(db, interaction.user.id)
            if not db_admin or not db_admin.community_id:
                raise CustomException(
                    "Only registered server admins can create reports!"
                )

            name = interaction.user.display_name
            if db_admin.name != name:
                db_admin.name = name

            db_token = await create_token(
                db,
                params=schemas.ReportTokenCreateParams(
                    admin_id=db_admin.discord_id,
                    community_id=db_admin.community_id,
                ),
                by=name,
            )

            params = schemas.ReportCreateParams(
                **self.params.model_dump(exclude={"created_at"}),
                token_id=db_token.id,
            )

            await create_report(
                db,
                params=params,
                by=name,
            )

            view = discord.ui.LayoutView()
            view.add_item(get_success_container("Report created!"))
            await interaction.response.edit_message(view=view)
