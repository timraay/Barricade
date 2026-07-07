import re

import discord
from discord import ButtonStyle, Interaction

from barricade import schemas
from barricade.crud.reports import get_report_by_id
from barricade.crud.responses import bulk_get_response_stats
from barricade.db import session_factory
from barricade.discord.utils import CustomException, LayoutView, handle_error_wrap
from barricade.discord.views.report import get_plain_report_view
from barricade.enums import Emojis


class ReportPublicReviewButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"publicprr:(?P<command>\w+):(?P<report_id>\d+)",
):
    def __init__(
        self,
        button: discord.ui.Button,
        command: str,
        report_id: int,
    ):
        self.command = command
        self.report_id = report_id

        button.custom_id = f"publicprr:{self.command}:{self.report_id}"

        super().__init__(button)

    @classmethod
    async def from_custom_id(  # type: ignore
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
        /,
    ):
        return cls(
            button=item,
            command=match["command"],
            report_id=int(match["report_id"]),
        )

    @handle_error_wrap
    async def callback(self, interaction: Interaction):
        match self.command:
            case "refresh":
                await self.refresh_report_view(interaction)

            case _:
                raise ValueError(f"Unknown command: {self.command}")

    async def refresh_report_view(self, interaction: Interaction):
        async with session_factory() as db:
            db_report = await get_report_by_id(db, self.report_id, load_token=True)
            if not db_report:
                raise CustomException(
                    f"Report with ID {self.report_id} no longer exists!"
                )
            report = schemas.ReportWithToken.model_validate(db_report)
            stats = await bulk_get_response_stats(db, report.players)

        view = await get_report_public_review_view(report, stats=stats)
        await interaction.response.edit_message(view=view)


async def get_report_public_review_view(
    report: schemas.ReportWithToken,
    stats: dict[int, schemas.ResponseStats] | None = None,
) -> LayoutView:
    view = await get_plain_report_view(
        report=report,
        stats=stats,
        with_eos_ids=True,
        refresh_button=ReportPublicReviewButton(
            button=discord.ui.Button(
                emoji=Emojis.REFRESH,
                style=ButtonStyle.gray,
            ),
            command="refresh",
            report_id=report.id,
        ),
    )
    return view
