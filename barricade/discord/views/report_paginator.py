from functools import partial

import discord
from discord import Interaction
from sqlalchemy.ext.asyncio import AsyncSession

from barricade import schemas
from barricade.crud.responses import (
    bulk_get_response_stats,
    get_community_responses_to_report,
)
from barricade.crud.watchlists import filter_watchlisted_player_ids
from barricade.db import session_factory
from barricade.discord.utils import CallableButton, LayoutView
from barricade.discord.views.report_management import get_report_management_view
from barricade.discord.views.report_review import get_report_review_view


class PaginatedReportsView(LayoutView):
    def __init__(
        self, community: schemas.CommunityRef, reports: list[schemas.ReportWithToken]
    ):
        if not reports:
            raise ValueError("reports may not be empty")

        super().__init__(timeout=60 * 60)
        self.community = community
        self.reports = reports
        self.page = 0
        self.stats: dict[int, schemas.ResponseStats] = {}

    @property
    def requires_pagination(self) -> bool:
        return len(self.reports) > 1

    async def send(self, interaction: Interaction):
        view = await self.load_page(0)
        await interaction.response.send_message(view=view, ephemeral=True)

    async def edit(self, interaction: Interaction, page: int | None = None):
        if page is None:
            page = self.page
        view = await self.load_page(page)
        await interaction.response.edit_message(view=view)

    async def go_to_page(self, page: int, interaction: Interaction):
        await self.edit(interaction, page)

    async def go_first_page(self, interaction: Interaction):
        await self.edit(interaction, 0)

    async def go_last_page(self, interaction: Interaction):
        await self.edit(interaction, len(self.reports) - 1)

    async def load_page(self, page: int) -> LayoutView:
        if not (0 <= page < len(self.reports)):
            raise IndexError(f"Page {page} out of range")

        old_page = self.page
        try:
            self.page = page
            report = self.reports[page]
            missing_stats = [pr for pr in report.players if pr.id not in self.stats]

            async with session_factory() as db:
                if missing_stats:
                    await self.fetch_response_stats(db, *missing_stats)

                # Get default view
                if report.token.community_id == self.community.id:
                    # Community submitted the report, use management view
                    view = await get_report_management_view(
                        report,
                        stats=self.stats,
                        with_refresh_button=not self.requires_pagination,
                    )
                else:
                    # Community did not submit the report, use review view

                    # Fetch responses
                    db_responses = await get_community_responses_to_report(
                        db, report, self.community.id
                    )
                    responses = self.get_pending_responses(
                        [
                            schemas.Response.model_validate(db_response)
                            for db_response in db_responses
                        ]
                    )
                    await self.fetch_response_stats(db, *missing_stats)

                    # Fetch watchlisted players
                    watchlisted_player_ids = await filter_watchlisted_player_ids(
                        db,
                        player_ids=[player.player_id for player in report.players],
                        community_id=self.community.id,
                    )

                    view = await get_report_review_view(
                        report,
                        responses,
                        watchlisted_player_ids,
                        stats=self.stats,
                        with_refresh_button=not self.requires_pagination,
                    )

            # If we have only one report, we do not need to add pagination.
            if not self.requires_pagination:
                return view

            # Remove existing items
            self.clear_items()

            # Inherit items from report view
            for item in view.children:
                self.add_item(item)

            # Add pagination buttons
            action_row = discord.ui.ActionRow()
            action_row.add_item(
                CallableButton(
                    self.go_first_page,
                    style=discord.ButtonStyle.blurple,
                    label="<<",
                    row=0,
                    disabled=page <= 0,
                )
            )
            action_row.add_item(
                CallableButton(
                    partial(self.go_to_page, page - 1),
                    style=discord.ButtonStyle.blurple,
                    label="<",
                    row=0,
                    disabled=page <= 0,
                )
            )
            action_row.add_item(
                discord.ui.Button(
                    style=discord.ButtonStyle.gray,
                    label=f"Report {self.page + 1} of {len(self.reports)}",
                    row=0,
                    disabled=True,
                )
            )
            action_row.add_item(
                CallableButton(
                    partial(self.go_to_page, page + 1),
                    style=discord.ButtonStyle.blurple,
                    label=">",
                    row=0,
                    disabled=page + 1 >= len(self.reports),
                )
            )
            action_row.add_item(
                CallableButton(
                    self.go_last_page,
                    style=discord.ButtonStyle.blurple,
                    label=">>",
                    row=0,
                    disabled=page + 1 >= len(self.reports),
                )
            )
            self.add_item(action_row)

            return self

        except Exception:
            self.page = old_page
            raise

    async def fetch_response_stats(
        self, db: AsyncSession, *player_reports: schemas.PlayerReportRef
    ):
        stats = await bulk_get_response_stats(db, player_reports)
        self.stats.update(stats)

    def get_pending_responses(self, responses: list[schemas.Response]):
        pending = {
            pr.id: schemas.PendingResponse(
                pr_id=pr.id,
                community_id=self.community.id,
                player_report=pr,
                community=self.community,
            )
            for pr in self.reports[self.page].players
        }
        for response in responses:
            pending[response.pr_id].banned = response.banned
            pending[response.pr_id].reject_reason = response.reject_reason
            pending[response.pr_id].responded_at = response.responded_at
            pending[response.pr_id].responded_by = response.responded_by
        return list(pending.values())
