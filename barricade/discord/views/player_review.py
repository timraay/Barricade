import functools
import random
import re
from sqlalchemy import select
from typing import TYPE_CHECKING, Any, Callable, Concatenate, Coroutine, Optional

import discord
from discord import ButtonStyle, Interaction

from barricade import schemas
from barricade.constants import T17_SUPPORT_CONFIRMATION_PROMPT_CHANCE, T17_SUPPORT_REASON_MASK
from barricade.crud.communities import get_community_by_id
from barricade.crud.reports import get_report_by_id
from barricade.crud.responses import bulk_get_response_stats, get_pending_responses, set_report_response
from barricade.db import models, session_factory
from barricade.discord.communities import assert_has_admin_role
from barricade.discord.utils import CallableButton, CustomException, View, get_command_mention, handle_error_wrap
from barricade.discord.reports import get_report_embed
from barricade.enums import Emojis, ReportRejectReason
from barricade.logger import get_logger

def random_ask_confirmation(func: Callable[Concatenate['PlayerReportResponseButton', Interaction, bool, ...], Coroutine[Any, Any, None]]
                            ) -> Callable[Concatenate['PlayerReportResponseButton', Interaction, bool, ...], Coroutine[Any, Any, None]]:
    @functools.wraps(func)
    async def wrapper(self: 'PlayerReportResponseButton', interaction: Interaction, banned: bool) -> None:
        if (
            banned
            and interaction.message is not None
            and random.random() < T17_SUPPORT_CONFIRMATION_PROMPT_CHANCE
        ):
            async with session_factory() as db:
                db_report = await get_report_by_id(db, self.report_id)
                if db_report and (db_report.reasons_bitflag & T17_SUPPORT_REASON_MASK) != 0:
                    async def inner(_interaction: Interaction):
                        await func(self, _interaction, banned, _original_interaction=interaction)
                    
                    embed = discord.Embed(description="To protect the players, we ask you to review all reports independently before sanctioning. Please confirm below if or when you have done so.")
                    embed.set_author(name="Did you review the evidence?")
                    view = View(timeout=600)
                    view.add_item(CallableButton(inner, label="Confirm", single_use=True))
                    return await interaction.response.send_message(
                        embed=embed,
                        view=view,
                        ephemeral=True,
                    )

        return await func(self, interaction, banned)
    return wrapper

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

    if TYPE_CHECKING:
        async def set_response(self, interaction: Interaction, banned: bool, *, _original_interaction: discord.Interaction | None = None) -> None:
            ...
    else:
        @random_ask_confirmation
        async def set_response(self, interaction: Interaction, banned: bool, *, _original_interaction: discord.Interaction | None = None):
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
                stats = await bulk_get_response_stats(db, report.players)

            selected = list(responses.keys()).index(prr.pr_id)
            responses = list(responses.values())
            view = PlayerReviewView(responses=responses, selected=selected)
            embed = await PlayerReviewView.get_embed(report, responses, stats=stats)

            if _original_interaction:
                if _original_interaction.message:
                    await _original_interaction.message.edit(embed=embed, view=view)
                await _original_interaction.delete_original_response()
                await interaction.response.defer()
            else:
                await interaction.response.edit_message(embed=embed, view=view)
    
    async def refresh_report_view(self, interaction: Interaction):
        async with session_factory() as db:
            db_report = await get_report_by_id(db, self.report_id, load_token=True)
            if not db_report:
                raise CustomException("Report with ID %s no longer exists!" % self.report_id)
            report = schemas.ReportWithToken.model_validate(db_report)

            db_community = await get_community_by_id(db, self.community_id)
            community = schemas.Community.model_validate(db_community)
            stats = await bulk_get_response_stats(db, report.players)
            responses = await get_pending_responses(db, community, report.players)
        
        # try:
        #     selected = [response.pr_id for response in responses].index(self.pr_id)
        # except ValueError:
        #     selected = 0

        view = PlayerReviewView(responses=responses)
        embed = await PlayerReviewView.get_embed(report, responses, stats=stats)
        await interaction.response.edit_message(embed=embed, view=view)

class PlayerReportSelect(
    discord.ui.DynamicItem[discord.ui.Select],
    template=r"prs:(?P<community_id>\d+):(?P<report_id>\d+)"
):
    def __init__(
        self,
        select: discord.ui.Select,
        community_id: int,
        report_id: int,
    ):
        self.community_id = community_id
        self.report_id = report_id

        select.custom_id = f"prs:{self.community_id}:{self.report_id}"
        
        super().__init__(select)
        self.select = select
    
    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Select, match: re.Match[str], /): # type: ignore
        return cls(
            select=item,
            community_id=int(match["community_id"]),
            report_id=int(match["report_id"]),
        )
    
    @handle_error_wrap
    async def callback(self, interaction: Interaction):
        async with session_factory() as db:
            db_community = await get_community_by_id(db, self.community_id)
            if not db_community:
                raise CustomException("Community not found")
            community = schemas.CommunityRef.model_validate(db_community)

            await assert_has_admin_role(interaction.user, community) # type: ignore

            db_report = await get_report_by_id(db, self.report_id, load_token=True)
            if not db_report:
                raise CustomException("Report with ID %s no longer exists!" % self.report_id)
            report = schemas.ReportWithToken.model_validate(db_report)
            stats = await bulk_get_response_stats(db, report.players)
            responses = await get_pending_responses(db, community, report.players)
        selected = int(self.select.values[0])
        if selected >= len(responses):
            get_logger(self.community_id).warning(
                "Selected index %s but there are only %s responses. Defaulting to 0.",
                selected, len(responses),
            )
        view = PlayerReviewView(responses, selected)
        embed = await view.get_embed(report, responses, stats=stats)
        await interaction.response.edit_message(embed=embed, view=view)

class PlayerReviewView(View):
    def __init__(self, responses: list[schemas.PendingResponse], selected: int = 0):
        if not responses:
            raise ValueError("Must have at least one response")
        
        super().__init__(timeout=None)

        community_id = responses[0].community_id
        report_id = responses[0].player_report.report_id

        is_multi = len(responses) > 1
        if is_multi:
            self.add_item(
                PlayerReportSelect(
                    select=discord.ui.Select(
                        options=[
                            discord.SelectOption(
                                label=f"{response.player_report.player_name}",
                                value=str(i),
                                description=f"Reviewed by {response.responded_by}" if response.responded_by else None,
                                emoji=Emojis.BANNED if response.banned else Emojis.UNBANNED if response.banned is False else Emojis.SILHOUETTE,
                                default=(i == selected),
                            )
                            for i, response in enumerate(responses)
                        ],
                        row=0,
                    ),
                    community_id=community_id,
                    report_id=report_id
                )
            )

        response = responses[selected]

        if response.banned is True:
            self.add_item(
                PlayerReportResponseButton(
                    button=discord.ui.Button(
                        label="Unban player",
                        style=ButtonStyle.blurple,
                        disabled=False,
                        row=1
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
                        label="Ban player",
                        style=ButtonStyle.red if response.banned is None else ButtonStyle.gray,
                        disabled=False,
                        row=1
                    ),
                    command="ban",
                    community_id=response.community_id,
                    report_id=response.player_report.report_id,
                    pr_id=response.pr_id,
                )
            )


        for reason, label in (
            (ReportRejectReason.INCONCLUSIVE, "Lacks evidence"),
            (ReportRejectReason.INSUFFICIENT, "Not severe enough"),
        ):
            if response.banned is None:
                button_style = ButtonStyle.blurple
            elif response.reject_reason == reason:
                button_style = ButtonStyle.green
            else:
                button_style = ButtonStyle.gray

            self.add_item(
                PlayerReportResponseButton(
                    button=discord.ui.Button(
                        label=label,
                        style=button_style,
                        disabled=response.banned is False,
                        row=1
                    ),
                    command="reject",
                    community_id=response.community_id,
                    report_id=response.player_report.report_id,
                    pr_id=response.pr_id,
                    reject_reason=reason
                )
            )

        if not is_multi:
            self.add_item(
                PlayerReportResponseButton(
                    button=discord.ui.Button(
                        emoji=Emojis.REFRESH,
                        style=ButtonStyle.gray,
                        row=1
                    ),
                    command="refresh",
                    community_id=response.community_id,
                    report_id=response.player_report.report_id,
                    pr_id=response.pr_id,
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
