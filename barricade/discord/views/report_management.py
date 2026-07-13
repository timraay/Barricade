import contextlib
import re

import discord
from discord import ButtonStyle, Interaction

from barricade import schemas
from barricade.crud.reports import delete_report, get_report_by_id
from barricade.crud.responses import bulk_get_response_stats
from barricade.db import session_factory
from barricade.discord.communities import assert_has_admin_role
from barricade.discord.utils import (
    CallableButton,
    CustomException,
    LayoutView,
    View,
    get_danger_embed,
    get_success_embed,
    handle_error_wrap,
)
from barricade.discord.views.report import get_plain_report_view
from barricade.discord.views.report_edit import ReportEditView
from barricade.enums import Emojis


class ReportManagementButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"rm:(?P<command>\w+):(?P<report_id>\d+)",
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
        await interaction.response.defer(ephemeral=True)

        async with session_factory.begin() as db:
            db_report = await get_report_by_id(db, self.report_id, load_token=True)
            if not db_report:
                raise CustomException(
                    "Report not found!", "This report was most likely deleted already."
                )
            report = schemas.ReportWithToken.model_validate(db_report)
            if interaction.user.id != report.token.admin_id:
                assert_has_admin_role(
                    interaction.user, report.token.community, report.game
                )

            match self.command:
                case "refresh":
                    stats = await bulk_get_response_stats(db, report.players)
                    view = await get_report_management_view(report, stats=stats)
                    await interaction.edit_original_response(
                        view=view, embed=None, content=None
                    )

                case "del":

                    async def confirm_delete(_interaction: Interaction):
                        await _interaction.response.defer(ephemeral=True)

                        async with session_factory.begin() as _db:
                            await delete_report(
                                _db, self.report_id, by=interaction.user
                            )

                        with contextlib.suppress(discord.NotFound):
                            await interaction.message.delete()  # type: ignore

                        await _interaction.edit_original_response(
                            embed=get_success_embed(
                                f"Report #{self.report_id} deleted!"
                            ),
                            view=None,
                        )

                    view = View()
                    view.add_item(
                        CallableButton(
                            confirm_delete,
                            style=ButtonStyle.red,
                            label="Delete Report",
                            single_use=True,
                        )
                    )
                    await interaction.followup.send(
                        embed=get_danger_embed(
                            "Are you sure you want to delete this report?",
                            "This action is irreversible.",
                        ),
                        view=view,
                        ephemeral=True,
                    )

                case "edit":
                    view = await ReportEditView.from_report(report)
                    await interaction.followup.send(view=view)

                case _:
                    raise ValueError(f"Unknown command {self.command!r}")


async def get_report_management_view(
    report: schemas.ReportWithToken,
    stats: dict[int, schemas.ResponseStats] | None = None,
    with_refresh_button: bool = True,
) -> LayoutView:
    action_row = discord.ui.ActionRow()

    action_row.add_item(
        ReportManagementButton(
            button=discord.ui.Button(
                style=discord.ButtonStyle.blurple,
                label="Edit report",
            ),
            command="edit",
            report_id=report.id,
        )
    )
    action_row.add_item(
        ReportManagementButton(
            button=discord.ui.Button(
                style=discord.ButtonStyle.red,
                label="Delete report",
            ),
            command="del",
            report_id=report.id,
        )
    )

    # Create view
    view = await get_plain_report_view(
        report,
        stats=stats,
        container_color=discord.Colour(0xFEE75C),  # yellow
        action_row=action_row,
        refresh_button=ReportManagementButton(
            button=discord.ui.Button(
                style=discord.ButtonStyle.grey,
                emoji=Emojis.REFRESH,
            ),
            command="refresh",
            report_id=report.id,
        )
        if with_refresh_button
        else None,
    )

    return view
