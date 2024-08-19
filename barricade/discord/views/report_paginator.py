import discord
from discord import Interaction
from sqlalchemy.ext.asyncio import AsyncSession

from barricade import schemas
from barricade.crud.responses import get_community_responses_to_report, get_response_stats
from barricade.db import session_factory
from barricade.discord.reports import get_report_embed
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
        self.page = 0
        self.stats: dict[int, schemas.ResponseStats] = {}

    async def send(self, interaction: Interaction):
        await self.load_page(0)
        embed = await get_report_embed(self.reports[self.page], stats=self.stats)
        await interaction.response.send_message(embed=embed, view=self, ephemeral=True)
    
    async def edit(self, interaction: Interaction):
        embed = await get_report_embed(self.reports[self.page], stats=self.stats)
        await interaction.response.edit_message(embed=embed, view=self)

    async def go_first_page(self, interaction: Interaction):
        await self.load_page(0)
        await self.edit(interaction)

    async def go_last_page(self, interaction: Interaction):
        await self.load_page(len(self.reports) - 1)
        await self.edit(interaction)

    async def go_page_backward(self, interaction: Interaction):
        await self.load_page(self.page - 1)
        await self.edit(interaction)

    async def go_page_forward(self, interaction: Interaction):
        await self.load_page(self.page + 1)
        await self.edit(interaction)

    async def load_page(self, page: int):
        if not (0 <= page < len(self.reports)):
            raise IndexError("Page %s out of range" % page)

        old_page = self.page
        try:
            # Remove existing items
            self.clear_items()

            # Add paginator menu
            if len(self.reports) > 1:
                self.add_item(CallableButton(self.go_first_page, style=discord.ButtonStyle.gray, label="<<", row=0))
                self.add_item(CallableButton(self.go_page_backward, style=discord.ButtonStyle.gray, label="<", row=0))
                self.add_item(discord.ui.Button(style=discord.ButtonStyle.gray, label=f"{self.page + 1}/{len(self.reports)}", row=0, disabled=True))
                self.add_item(CallableButton(self.go_page_forward, style=discord.ButtonStyle.gray, label=">", row=0))
                self.add_item(CallableButton(self.go_last_page, style=discord.ButtonStyle.gray, label=">>", row=0))

            self.page = page
            report = self.reports[page]
            missing_stats = [pr for pr in report.players if pr.id not in self.stats]
            
            # Get default view
            if report.token.community_id == self.community.id:
                view = ReportManagementView(report)
                if missing_stats:
                    async with session_factory() as db:
                        await self.fetch_response_stats(db, *missing_stats)
            else:
                async with session_factory() as db:
                    # Load responses
                    db_responses = await get_community_responses_to_report(db, report, self.community.id)
                    responses = self.get_pending_responses([
                        schemas.Response.model_validate(db_response)
                        for db_response in db_responses
                    ])
                    await self.fetch_response_stats(db, *missing_stats)
                view = PlayerReviewView(responses)
            
            # Add items from default view to paginator
            for item in view.children:
                self.add_item(item)

        except:
            self.page = old_page
            raise

    async def fetch_response_stats(self, db: AsyncSession, *player_reports: schemas.PlayerReportRef):
        for pr in player_reports:
            stats = await get_response_stats(db, pr)
            self.stats[pr.id] = stats

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
        return list(pending.values())
