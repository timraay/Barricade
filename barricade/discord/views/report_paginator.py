import copy
from functools import partial
import discord
from discord import Embed, Interaction
from sqlalchemy.ext.asyncio import AsyncSession

from barricade import schemas
from barricade.crud.responses import bulk_get_response_stats, get_community_responses_to_report
from barricade.crud.watchlists import filter_watchlisted_player_ids
from barricade.db import session_factory
from barricade.discord.utils import View, CallableButton
from barricade.discord.views.report_management import ReportManagementView
from barricade.discord.views.player_review import PlayerReviewView

class ReportPaginator(View):
    def __init__(self, community: schemas.CommunityRef, reports: list[schemas.ReportWithToken]):
        if not reports:
            raise ValueError("reports may not be empty")

        super().__init__(timeout=60*60)
        self.community = community
        self.reports = reports
        self.requires_pagination = len(self.reports) > 1
        self.page = 0
        self.stats: dict[int, schemas.ResponseStats] = {}

    async def send(self, interaction: Interaction):
        embed, view = await self.load_page(0)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
    
    async def edit(self, interaction: Interaction, page: int | None = None):
        if page is None:
            page = self.page
        embed, view = await self.load_page(page)
        await interaction.response.edit_message(embed=embed, view=view)

    async def go_to_page(self, page: int, interaction: Interaction):
        await self.edit(interaction, page)

    async def go_first_page(self, interaction: Interaction):
        await self.edit(interaction, 0)

    async def go_last_page(self, interaction: Interaction):
        await self.edit(interaction, len(self.reports) - 1)

    async def load_page(self, page: int) -> tuple[Embed, View]:
        if not (0 <= page < len(self.reports)):
            raise IndexError("Page %s out of range" % page)

        old_page = self.page
        try:
            self.page = page
            report = self.reports[page]
            missing_stats = [pr for pr in report.players if pr.id not in self.stats]
            
            # Get default view
            if report.token.community_id == self.community.id:
                view = ReportManagementView(report)
                if missing_stats:
                    async with session_factory() as db:
                        await self.fetch_response_stats(db, *missing_stats)
                embed = await view.get_embed(self.reports[self.page], stats=self.stats)
            else:
                async with session_factory() as db:
                    # Load responses
                    db_responses = await get_community_responses_to_report(db, report, self.community.id)
                    responses = self.get_pending_responses([
                        schemas.Response.model_validate(db_response)
                        for db_response in db_responses
                    ])
                    await self.fetch_response_stats(db, *missing_stats)
                    
                    watchlisted_player_ids = await filter_watchlisted_player_ids(
                        db,
                        player_ids=[player.player_id for player in report.players],
                        community_id=self.community.id,
                    )
                view = PlayerReviewView(responses, watchlisted_player_ids)
                embed = await view.get_embed(self.reports[self.page], responses=responses, stats=self.stats)

            # If we have only one report, we do not need to add pagination.
            if not self.requires_pagination:
                return embed, view

            # Remove existing items
            self.clear_items()

            # Add paginator menu
            if self.requires_pagination:
                self.add_item(CallableButton(
                    self.go_first_page,
                    style=discord.ButtonStyle.blurple,
                    label="<<",
                    row=0,
                    disabled=page <= 0
                ))
                self.add_item(CallableButton(
                    partial(self.go_to_page, page - 1),
                    style=discord.ButtonStyle.blurple,
                    label="<",
                    row=0,
                    disabled=page <= 0
                ))
                self.add_item(discord.ui.Button(
                    style=discord.ButtonStyle.gray,
                    label=f"Report {self.page + 1} of {len(self.reports)}",
                    row=0,
                    disabled=True
                ))
                self.add_item(CallableButton(
                    partial(self.go_to_page, page + 1),
                    style=discord.ButtonStyle.blurple,
                    label=">",
                    row=0,
                    disabled=page + 1 >= len(self.reports)
                ))
                self.add_item(CallableButton(
                    self.go_last_page,
                    style=discord.ButtonStyle.blurple,
                    label=">>",
                    row=0,
                    disabled=page + 1 >= len(self.reports)
                ))

            # Place original view below paginator menu
            for item in view.children:
                if item.row is not None:
                    item = copy.copy(item)
                    assert item.row is not None
                    item.row += 1
                self.add_item(item)

            return embed, self

        except Exception:
            self.page = old_page
            raise

    async def fetch_response_stats(self, db: AsyncSession, *player_reports: schemas.PlayerReportRef):
        stats = await bulk_get_response_stats(db, player_reports)
        self.stats.update(stats)

    def get_pending_responses(self, responses: list[schemas.Response]):
        pending = {
            pr.id: schemas.PendingResponse(
                pr_id=pr.id,
                community_id=self.community.id,
                player_report=pr,
                community=self.community
            )
            for pr in self.reports[self.page].players
        }
        for response in responses:
            pending[response.pr_id].banned = response.banned
            pending[response.pr_id].reject_reason = response.reject_reason
            pending[response.pr_id].responded_by = response.responded_by
        return list(pending.values())
