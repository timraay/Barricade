import re
from sqlalchemy import select
from typing import Optional

import discord
from discord import ButtonStyle, Interaction

from bunker import schemas
from bunker.crud.communities import get_community_by_id
from bunker.crud.reports import get_report_by_id
from bunker.crud.responses import get_pending_responses, get_response_stats, set_report_response
from bunker.db import models, session_factory
from bunker.discord.utils import View
from bunker.discord.reports import get_report_embed
from bunker.enums import ReportRejectReason

class PlayerReportResponseButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"prr:(?P<command>\w+):(?P<community_id>\d+):(?P<pr_id>\d+)(?::(?P<reject_reason>[\w_]+))?"
):
    def __init__(
        self,
        button: discord.ui.Button,
        command: str,
        community_id: int,
        pr_id: int,
        reject_reason: Optional[ReportRejectReason] = None
    ):
        self.command = command
        self.community_id = community_id
        self.pr_id = pr_id
        self.reject_reason = reject_reason

        if self.reject_reason:
            button.custom_id = f"prr:{self.command}:{self.community_id}:{self.pr_id}:{self.reject_reason.name}",
        else:
            button.custom_id = f"prr:{self.command}:{self.community_id}:{self.pr_id}",
        
        super().__init__(button)
    
    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str], /):
        reject_reason = match["reject_reason"]
        if isinstance(reject_reason, str):
            reject_reason = ReportRejectReason[reject_reason]

        return cls(
            button=item,
            command=match["command"],
            community_id=int(match["community_id"]),
            pr_id=int(match["pr_id"]),
            reject_reason=reject_reason,
        )
    
    async def callback(self, interaction: Interaction):
        match self.command:
            case "refresh":
                await self.refresh_report_view(interaction)

            case "ban":
                await self.set_response(interaction, banned=True)

            case "unban":
                await self.set_response(interaction, banned=False)

            case _:
                await self.set_response(interaction, banned=False)


    async def set_response(self, interaction: Interaction, banned: bool):
        prr = schemas.ResponseCreateParams(
            pr_id=self.pr_id,
            community_id=self.community_id,
            banned=banned,
            reject_reason=self.reject_reason,
        )
        
        async with session_factory.begin() as db:
            db_prr = await set_report_response(db, prr)

            players: list[models.PlayerReport] = await db_prr.player_report.report.awaitable_attrs.players
            responses = {
                player.id: schemas.PendingResponse(
                    pr_id=player.id,
                    player_report=player,
                    community_id=db_prr.community_id,
                    community=db_prr.community,
                ) for player in players
            }
            responses[prr.pr_id].banned = prr.banned
            responses[prr.pr_id].reject_reason = prr.reject_reason

            if len(players) > 1 or players[0].id != prr.pr_id:
                # Load state of other reported players if needed
                stmt = select(
                    models.PlayerReportResponse.pr_id,
                    models.PlayerReportResponse.reject_reason,
                    models.PlayerReportResponse.banned
                ).join(
                    models.PlayerReport
                ).where(
                    models.PlayerReportResponse.community_id == prr.community_id,
                    models.PlayerReport.id.in_(
                        [player.id for player in players if player.id != prr.pr_id]
                    )
                )
                result = await db.execute(stmt)
                for row in result:
                    responses[row.pr_id].banned = row.banned
                    responses[row.pr_id].reject_reason = row.reject_reason

            report = db_prr.player_report.report
            await report.awaitable_attrs.token

            stats: dict[int, schemas.ResponseStats] = {}
            for player in report.players:
                stats[player.id] = await get_response_stats(db, player)

        responses = list(responses.values())
        view = PlayerReviewView(responses=responses)
        embed = await PlayerReviewView.get_embed(report, responses, stats=stats)
        await interaction.response.edit_message(embed=embed, view=view)
    
    async def refresh_report_view(self, interaction: Interaction):
        async with session_factory() as db:
            # In this case, pr_id is actually the report ID, not the player report ID
            report = await get_report_by_id(db, self.pr_id, load_token=True)
            community = await get_community_by_id(db, self.community_id)

            stats: dict[int, schemas.ResponseStats] = {}
            for player in report.players:
                stats[player.id] = await get_response_stats(db, player)

            responses = await get_pending_responses(db, community, report.players)
        view = PlayerReviewView(responses=responses)
        embed = await PlayerReviewView.get_embed(report, responses, stats=stats)
        await interaction.response.edit_message(embed=embed, view=view)

class PlayerReviewView(View):
    def __init__(self, responses: list[schemas.PendingResponse]):
        super().__init__(timeout=None)

        for row, response in enumerate(responses):
            self.add_item(
                PlayerReportResponseButton(
                    button=discord.ui.Button(
                        label=f"# {row + 1}.",
                        style=ButtonStyle.red if response.banned is True else ButtonStyle.gray,
                        row=row
                    ),
                    command="refresh",
                    community_id=response.community_id,
                    pr_id=response.pr_id,

                )
            )

            if response.banned is True:
                self.add_item(
                    PlayerReportResponseButton(
                        button=discord.ui.Button(
                            label="Unban player",
                            style=ButtonStyle.blurple,
                            disabled=False,
                            row=row
                        ),
                        command="unban",
                        community_id=response.community_id,
                        pr_id=response.pr_id,

                    )
                )
            else:
                self.add_item(
                    PlayerReportResponseButton(
                        button=discord.ui.Button(
                            label="Ban player...",
                            style=ButtonStyle.red,
                            disabled=False,
                            row=row
                        ),
                        command="ban",
                        community_id=response.community_id,
                        pr_id=response.pr_id,

                    )
                )


            for reason in ReportRejectReason:
                if response.banned is None:
                    button_style = ButtonStyle.blurple
                if response.reject_reason == reason:
                    button_style = ButtonStyle.green
                else:
                    button_style = ButtonStyle.gray

                self.add_item(
                    PlayerReportResponseButton(
                        button=discord.ui.Button(
                            label=reason.value,
                            style=button_style,
                            disabled=response.banned is False,
                            row=row
                        ),
                        command="reject",
                        community_id=response.community_id,
                        pr_id=response.pr_id,
                        reject_reason=reason
                    )
                )

    @staticmethod
    async def get_embed(
        report: schemas.ReportWithToken,
        responses: list[schemas.PendingResponse],
        stats: dict[int, schemas.ResponseStats] = None
    ):
        embed = await get_report_embed(report, stats)
        if len(responses) == len(report.players):
            if any(response.banned for response in responses):
                embed.color = discord.Colour.brand_red()
            else:
                embed.color = discord.Colour.brand_green()
        return embed
