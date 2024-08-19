import re
from datetime import datetime, timezone

import discord
from discord import ButtonStyle, Interaction

from barricade import schemas
from barricade.constants import REPORT_TOKEN_EXPIRE_DELTA
from barricade.crud.reports import delete_report, get_report_by_id
from barricade.db import session_factory
from barricade.discord.reports import get_report_embed
from barricade.discord.utils import CallableButton, View, format_url, get_danger_embed, get_success_embed, handle_error_wrap
from barricade.exceptions import NotFoundError
from barricade.urls import get_report_edit_url

class ReportManagementButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"rm:(?P<command>\w+):(?P<report_id>\d+)"
):
    def __init__(
        self,
        button: discord.ui.Button,
        command: str,
        report_id: int,
    ):
        self.command = command
        self.report_id = report_id
        
        button.custom_id = f"rm:{self.command}:{self.report_id}"
        
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
        async with session_factory.begin() as db:
            match self.command:
                case "del":
                    # TODO? Only allow admins to delete
                    async def confirm_delete(_interaction: Interaction):
                        await delete_report(db, self.report_id, by=interaction.user) # type: ignore
                        await interaction.message.delete() # type: ignore
                        await _interaction.response.edit_message(
                            embed=get_success_embed(f"Report #{self.report_id} deleted!"),
                            view=None
                        )

                    view = View()
                    view.add_item(
                        CallableButton(confirm_delete, style=ButtonStyle.red, label="Delete Report")
                    )
                    await interaction.response.send_message(
                        embed=get_danger_embed(
                            "Are you sure you want to delete this report?",
                            "This action is irreversible."
                        )
                    )

                case "edit":
                    db_report = await get_report_by_id(db, self.report_id, load_token=True)
                    if not db_report:
                        raise NotFoundError("This report no longer exists")
                    
                    # Generate new token and update expiration date
                    db_report.token.value = db_report.token.generate_value()
                    db_report.token.expires_at = datetime.now(tz=timezone.utc) + REPORT_TOKEN_EXPIRE_DELTA
                    # Send URL to user
                    url = get_report_edit_url(schemas.ReportWithToken.model_validate(db_report))
                    await interaction.response.send_message(
                        content="## " + format_url("Open Google Form", url),
                        ephemeral=True
                    )

                case _:
                    raise ValueError("Unknown command %s" % self.command)

class ReportManagementView(View):
    def __init__(self, report: schemas.ReportRef):
        super().__init__(timeout=None)
        self.add_item(ReportManagementButton(
            button=discord.ui.Button(
                style=discord.ButtonStyle.blurple,
                label="Edit report",
            ),
            command="edit",
            report_id=report.id
        ))
        self.add_item(ReportManagementButton(
            button=discord.ui.Button(
                style=discord.ButtonStyle.red,
                label="Delete report",
            ),
            command="del",
            report_id=report.id
        ))

    @staticmethod
    async def get_embed(
        report: schemas.ReportWithToken,
        stats: dict[int, schemas.ResponseStats] | None = None
    ):
        embed = await get_report_embed(report, stats=stats, with_footer=False)
        embed.color = discord.Colour(0xfee75c) # yellow
        return embed
    
