import re
from sqlalchemy import select
from typing import Optional

import discord
from discord import ButtonStyle, Interaction

from barricade import schemas
from barricade.crud.communities import get_community_by_id
from barricade.crud.reports import get_report_by_id
from barricade.crud.responses import get_pending_responses, get_response_stats, set_report_response
from barricade.db import models, session_factory
from barricade.discord.communities import assert_has_admin_role
from barricade.discord.utils import CustomException, View, get_command_mention, get_danger_embed, handle_error_wrap
from barricade.discord.reports import get_report_embed
from barricade.enums import ReportRejectReason

class PlayerReportResponseButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"prr:(?P<command>\w+):(?P<community_id>\d+):(?P<report_id>\d+):(?P<pr_id>\d+)(?::(?P<reject_reason>[\w_]+))?"
):
    def __init__(
        self,
        button: discord.ui.Button,
        command: str,
        community_id: int,
        report_id: int,
        pr_id: int,
        reject_reason: Optional[ReportRejectReason] = None
    ):
        self.command = command
        self.community_id = community_id
        self.report_id = report_id
        self.pr_id = pr_id
        self.reject_reason = reject_reason

        if self.reject_reason:
            button.custom_id = f"prr:{self.command}:{self.community_id}:{self.report_id}:{self.pr_id}:{self.reject_reason.name}"
        else:
            button.custom_id = f"prr:{self.command}:{self.community_id}:{self.report_id}:{self.pr_id}"
        
        super().__init__(button)
    
    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str], /): # type: ignore
        reject_reason = match["reject_reason"]
        if isinstance(reject_reason, str):
            reject_reason = ReportRejectReason[reject_reason]

        return cls(
            button=item,
            command=match["command"],
            community_id=int(match["community_id"]),
            report_id=int(match["report_id"]),
            pr_id=int(match["pr_id"]),
            reject_reason=reject_reason,
        )
    
    @handle_error_wrap
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
            responded_by=interaction.user.display_name,
        )
        
        async with session_factory() as db:
            db_community = await get_community_by_id(db, self.community_id)
            if not db_community:
                raise CustomException("Community not found")
            
            await assert_has_admin_role(
                interaction.user, # type: ignore
                schemas.CommunityRef.model_validate(db_community),
            )
            
            # Make sure that there is at least one enabled integration
            if banned:
                is_owner = db_community.owner_id == interaction.user.id
                err_msg = None
                if not db_community.integrations:
                    err_msg = "No integrations have been added yet!"
                elif not any(integration.enabled for integration in db_community.integrations):
                    err_msg = "No integrations are enabled!"

                if err_msg:
                    if is_owner:
                        raise CustomException(
                            err_msg,
                            (
                                "Integrations are necessary to connect to your game servers and, by extension, ban players."
                                "\n\n"
                                f"You can manage integrations using {await get_command_mention(interaction.client.tree, 'config', 'integrations')}." # type: ignore
                                " For more information on how to setup an integration, please refer to [these instructions]"
                                "(https://github.com/timraay/Barricade/wiki/Quickstart#3-connecting-to-your-game-servers)."
                            )
                        )
                    else:
                        raise CustomException(
                            err_msg,
                            (
                                "Integrations are necessary to connect to your game servers and, by extension, ban players."
                                "\n\n"
                                "Only the owner of your community can manage integrations. Refer them to [these instructions]"
                                "(https://github.com/timraay/Barricade/wiki/Quickstart#3-connecting-to-your-game-servers)."
                            )
                        )

            # This will immediately commit
            db.expire_all()
            db_prr = await set_report_response(db, prr)

            db_players: list[models.PlayerReport] = await db_prr.player_report.report.awaitable_attrs.players
            community = schemas.CommunityRef.model_validate(db_prr.community)

            responses = {
                player.id: schemas.PendingResponse(
                    pr_id=player.id,
                    player_report=schemas.PlayerReportRef.model_validate(player),
                    community_id=db_prr.community_id,
                    community=community,
                ) for player in db_players
            }
            responses[prr.pr_id].banned = prr.banned
            responses[prr.pr_id].reject_reason = prr.reject_reason
            responses[prr.pr_id].responded_by = prr.responded_by

            if len(db_players) > 1 or db_players[0].id != prr.pr_id:
                # Load state of other reported players if needed
                stmt = select(
                    models.PlayerReportResponse.pr_id,
                    models.PlayerReportResponse.reject_reason,
                    models.PlayerReportResponse.banned,
                    models.PlayerReportResponse.responded_by,
                ).join(
                    models.PlayerReport
                ).where(
                    models.PlayerReportResponse.community_id == prr.community_id,
                    models.PlayerReport.id.in_(
                        [player.id for player in db_players if player.id != prr.pr_id]
                    )
                )
                result = await db.execute(stmt)
                for row in result:
                    responses[row.pr_id].banned = row.banned
                    responses[row.pr_id].reject_reason = row.reject_reason
                    responses[row.pr_id].responded_by = row.responded_by

            db_report = db_prr.player_report.report
            await db_report.awaitable_attrs.token
            report = schemas.ReportWithToken.model_validate(db_report)

            stats: dict[int, schemas.ResponseStats] = {}
            for player in report.players:
                stats[player.id] = await get_response_stats(db, player)

        responses = list(responses.values())
        view = PlayerReviewView(responses=responses)
        embed = await PlayerReviewView.get_embed(report, responses, stats=stats)
        await interaction.response.edit_message(embed=embed, view=view)
    
    async def refresh_report_view(self, interaction: Interaction):
        async with session_factory() as db:
            db_report = await get_report_by_id(db, self.report_id, load_token=True)
            if not db_report:
                raise CustomException("Report with ID %s no longer exists!" % self.pr_id)
            report = schemas.ReportWithToken.model_validate(db_report)

            db_community = await get_community_by_id(db, self.community_id)
            community = schemas.Community.model_validate(db_community)

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
                        style=(
                            ButtonStyle.red if response.banned is True else
                            ButtonStyle.green if response.banned is False else
                            ButtonStyle.gray
                        ),
                        row=row
                    ),
                    command="refresh",
                    community_id=response.community_id,
                    report_id=response.player_report.report_id,
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
                        report_id=response.player_report.report_id,
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
                        report_id=response.player_report.report_id,
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
                        report_id=response.player_report.report_id,
                        pr_id=response.pr_id,
                        reject_reason=reason
                    )
                )

    @staticmethod
    async def get_embed(
        report: schemas.ReportWithToken,
        responses: list[schemas.PendingResponse],
        stats: dict[int, schemas.ResponseStats] | None = None
    ):
        embed = await get_report_embed(report, responses=responses, stats=stats)

        if any(response.banned is None for response in responses):
            embed.color = discord.Colour.blurple()
        elif any(response.banned is True for response in responses):
            embed.color = discord.Colour(0x521616) # dark red
        elif all(response.banned is False for response in responses):
            embed.color = discord.Colour(0x253021) # dark green
        # default color is dark_theme

        return embed
