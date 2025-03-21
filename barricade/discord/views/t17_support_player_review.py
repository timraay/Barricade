import re

import discord
from discord import ButtonStyle, Interaction

from barricade import schemas
from barricade.crud.reports import get_report_by_id
from barricade.crud.responses import bulk_get_response_stats
from barricade.db import session_factory
from barricade.discord.utils import CustomException, View, handle_error_wrap
from barricade.discord.reports import get_report_embed
from barricade.enums import Emojis, ReportRejectReason

class T17SupportPlayerReportResponseButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"t17prr:(?P<command>\w+):(?P<report_id>\d+)"
):
    def __init__(
        self,
        button: discord.ui.Button,
        command: str,
        report_id: int,
    ):
        self.command = command
        self.report_id = report_id

        button.custom_id = f"t17prr:{self.command}:{self.report_id}"
        
        super().__init__(button)
    
    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str], /): # type: ignore
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
                raise ValueError("Unknown command: %s" % self.command)

    async def refresh_report_view(self, interaction: Interaction):
        async with session_factory() as db:
            db_report = await get_report_by_id(db, self.report_id, load_token=True)
            if not db_report:
                raise CustomException("Report with ID %s no longer exists!" % self.report_id)
            report = schemas.ReportWithToken.model_validate(db_report)
            stats = await bulk_get_response_stats(db, report.players)
        
        view = T17SupportPlayerReviewView(report=report)
        embed = await T17SupportPlayerReviewView.get_embed(report, stats=stats)
        await interaction.response.edit_message(embed=embed, view=view)

class T17SupportPlayerReviewView(View):
    def __init__(self, report: schemas.ReportWithToken):
        super().__init__(timeout=None)
        self.add_item(
            T17SupportPlayerReportResponseButton(
                button=discord.ui.Button(
                    emoji=Emojis.REFRESH,
                    style=ButtonStyle.gray,
                    row=1
                ),
                command="refresh",
                report_id=report.id,
            )
        )

    @staticmethod
    async def get_embed(
        report: schemas.ReportWithToken,
        stats: dict[int, schemas.ResponseStats] | None = None
    ):
        embed = await get_report_embed(report, stats=stats)
        return embed
