from typing import assert_never

import discord
from discord import ButtonStyle, Interaction

from barricade.crud.communities import get_admin_by_id
from barricade.db import session_factory
from barricade.discord.utils import CallableButton, CustomException, LayoutView
from barricade.discord.views.report_create import ReportCreateView
from barricade.enums import Platform


class ReportSubmissionStartView(LayoutView):
    def __init__(self, platform: Platform):
        super().__init__(timeout=None)
        self.platform = platform

        self.add_item(
            discord.ui.TextDisplay(
                "## Submitting a report"
                "\nHad a player significantly disrupt your server? Then submit a report to Barricade!"
                "\nYour evidence will be shared with other community admins, allowing them to"
                " preemptively ban the player and prevent them from repeating their actions elsewhere."
                "\n\n"
                "> Only severe violations should warrant getting someone banned across many community servers."
                "\n> As a rule of thumb, **only report players that do not deserve a second chance**."
            )
        )

        self.add_item(
            discord.ui.Separator(visible=False, spacing=discord.SeparatorSpacing.large)
        )

        container = discord.ui.Container()
        container.add_item(
            discord.ui.TextDisplay(
                "## Submit a report"
                "\n-# Reporting requires a **burden of proof**."
                "\n-# Reports with insufficient evidence are subject to removal."
            )
        )
        self.add_item(container)

        match platform:
            case Platform.PC:
                custom_id = "get_submission_url_pc"
            case Platform.CONSOLE:
                custom_id = "get_submission_url_console"
            case _:
                assert_never(platform)

        self.add_item(
            discord.ui.ActionRow(
                CallableButton(
                    self.start_submission,
                    style=ButtonStyle.blurple,
                    label="Submit a report",
                    custom_id=custom_id,
                )
            )
        )

    async def start_submission(self, interaction: Interaction):
        async with session_factory() as db:
            db_admin = await get_admin_by_id(db, interaction.user.id)
            if not db_admin or not db_admin.community_id:
                raise CustomException(
                    "Only registered server admins can create reports!"
                )

        await ReportCreateView.new(interaction)
