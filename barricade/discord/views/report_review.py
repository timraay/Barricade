import functools
import random
import re
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Concatenate

import discord
from discord import ButtonStyle, Interaction
from sqlalchemy import select

from barricade import schemas
from barricade.constants import (
    T17_SUPPORT_CONFIRMATION_PROMPT_CHANCE,
    T17_SUPPORT_REASON_MASK,
)
from barricade.crud.communities import get_community_by_id
from barricade.crud.reports import get_report_by_id, set_report_comment
from barricade.crud.responses import (
    bulk_get_response_stats,
    get_pending_responses,
    set_report_response,
)
from barricade.crud.watchlists import filter_watchlisted_player_ids
from barricade.db import models, session_factory
from barricade.discord.communities import assert_has_admin_role
from barricade.discord.crud_utils import get_community
from barricade.discord.utils import (
    CallableButton,
    CustomException,
    LayoutView,
    Modal,
    View,
    get_command_mention,
    handle_error_wrap,
)
from barricade.discord.views.player_watchlist import PlayerToggleWatchlistButton
from barricade.discord.views.report import get_plain_report_view
from barricade.enums import Emojis, ReportRejectReason


def random_ask_confirmation(
    func: Callable[
        Concatenate["ReportReviewButton", Interaction, bool, ...],
        Coroutine[Any, Any, None],
    ],
) -> Callable[
    Concatenate["ReportReviewButton", Interaction, bool, ...],
    Coroutine[Any, Any, None],
]:
    @functools.wraps(func)
    async def wrapper(
        self: "ReportReviewButton", interaction: Interaction, banned: bool
    ) -> None:
        # Randomly interrupt the standard flow to ask for a confirmation
        if (
            banned
            and interaction.message is not None
            and random.random() < T17_SUPPORT_CONFIRMATION_PROMPT_CHANCE
        ):
            async with session_factory() as db:
                # Fetch report to check whether it might be sent to T17 Support
                db_report = await get_report_by_id(db, self.report_id)
                if (
                    db_report
                    and (db_report.reasons_bitflag & T17_SUPPORT_REASON_MASK) != 0
                ):
                    # Wrap original function with new interaction context
                    async def inner(_interaction: Interaction):
                        await func(
                            self,
                            _interaction,
                            banned,
                            _original_interaction=interaction,
                        )

                    # Create new view with a single-use button that calls the original
                    # function in the updated context
                    view = View(timeout=600)
                    view.add_item(
                        CallableButton(inner, label="Confirm", single_use=True)
                    )

                    # Send confirmation message
                    embed = discord.Embed(
                        description="To protect the players, we ask you to review all reports independently before sanctioning. Please confirm below if or when you have done so."
                    )
                    embed.set_author(name="Did you review the evidence?")
                    await interaction.response.send_message(
                        embed=embed,
                        view=view,
                        ephemeral=True,
                    )
                    return

        # Call original function when no confirmation is needed
        return await func(self, interaction, banned)

    return wrapper


class ReportReviewButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"prr:(?P<command>\w+):(?P<community_id>\d+):(?P<report_id>\d+):(?P<pr_id>\d+)(?::(?P<reject_reason>[\w_]+))?",
):
    def __init__(
        self,
        button: discord.ui.Button,
        command: str,
        community_id: int,
        report_id: int,
        pr_id: int,
        reject_reason: ReportRejectReason | None = None,
    ):
        self.command = command
        self.community_id = community_id
        self.report_id = report_id
        self.pr_id = pr_id
        self.reject_reason = reject_reason

        if self.reject_reason:
            button.custom_id = f"prr:{self.command}:{self.community_id}:{self.report_id}:{self.pr_id}:{self.reject_reason.name}"
        else:
            button.custom_id = (
                f"prr:{self.command}:{self.community_id}:{self.report_id}:{self.pr_id}"
            )

        super().__init__(button)

    @classmethod
    async def from_custom_id(  # type: ignore
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
        /,
    ):
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

            case "watchlist":
                await self.set_response(interaction, banned=False)

            case "comment":
                await self.edit_comment(interaction)

            case _:
                await self.set_response(interaction, banned=False)

    if TYPE_CHECKING:

        async def set_response(
            self,
            interaction: Interaction,
            banned: bool,
            *,
            _original_interaction: discord.Interaction | None = None,
        ) -> None: ...
    else:

        @random_ask_confirmation
        async def set_response(
            self,
            interaction: Interaction,
            banned: bool,
            *,
            _original_interaction: discord.Interaction | None = None,
        ):
            prr = schemas.ResponseCreateParams(
                pr_id=self.pr_id,
                community_id=self.community_id,
                banned=banned,
                reject_reason=self.reject_reason,
                responded_at=datetime.now(tz=UTC),
                responded_by=interaction.user.mention if interaction.user else None,
            )
            async with session_factory() as db:
                db_report = await get_report_by_id(db, self.report_id)
                if not db_report:
                    raise CustomException(
                        f"Report with ID {self.report_id} no longer exists!"
                    )

                db_community = await get_community(db, self.community_id)
                community = schemas.Community.model_validate(db_community)
                assert isinstance(interaction.user, discord.Member)
                assert_has_admin_role(interaction.user, community, db_report.game)

                # Make sure that there is at least one enabled integration
                if banned:
                    err_msg = None
                    if not community.integrations:
                        err_msg = "No integrations have been added yet!"
                    elif not any(
                        integration.enabled for integration in community.integrations
                    ):
                        err_msg = "No integrations are enabled!"

                    if err_msg:
                        raise CustomException(
                            err_msg,
                            (
                                "Integrations are necessary to connect to your game servers and, by extension, ban players."
                                "\n\n"
                                f"You can manage integrations using {await get_command_mention(interaction.client.tree, 'config', 'integrations')}."  # type: ignore
                                " For more information on how to setup an integration, please refer to [these instructions]"
                                "(https://github.com/timraay/Barricade/wiki/Quickstart#3-connecting-to-your-game-servers)."
                            ),
                        )

                # Set the response
                # This will immediately commit
                db.expire_all()
                db_prr = await set_report_response(db, prr)

                # Load report and responses
                db_players: list[
                    models.PlayerReport
                ] = await db_prr.player_report.report.awaitable_attrs.players
                community = schemas.CommunityRef.model_validate(db_prr.community)

                # Start building pending responses map
                responses = {
                    player.id: schemas.PendingResponse(
                        pr_id=player.id,
                        player_report=schemas.PlayerReportRef.model_validate(player),
                        community_id=db_prr.community_id,
                        community=community,
                    )
                    for player in db_players
                }
                # Update the response that was just set
                responses[prr.pr_id].banned = prr.banned
                responses[prr.pr_id].reject_reason = prr.reject_reason
                responses[prr.pr_id].responded_by = prr.responded_by

                # Fetch response state of any other reported players
                if len(db_players) > 1 or db_players[0].id != prr.pr_id:
                    stmt = (
                        select(
                            models.PlayerReportResponse.pr_id,
                            models.PlayerReportResponse.reject_reason,
                            models.PlayerReportResponse.banned,
                            models.PlayerReportResponse.responded_by,
                        )
                        .join(models.PlayerReport)
                        .where(
                            models.PlayerReportResponse.community_id
                            == prr.community_id,
                            models.PlayerReport.id.in_(
                                [
                                    player.id
                                    for player in db_players
                                    if player.id != prr.pr_id
                                ]
                            ),
                        )
                    )
                    result = await db.execute(stmt)
                    for row in result:
                        responses[row.pr_id].banned = row.banned
                        responses[row.pr_id].reject_reason = row.reject_reason
                        responses[row.pr_id].responded_by = row.responded_by

                # Fetch report and response stats
                db_report = db_prr.player_report.report
                await db_report.awaitable_attrs.token
                db_report = schemas.ReportWithToken.model_validate(db_report)
                stats = await bulk_get_response_stats(db, db_report.players)

                # Fetch watchlisted player IDs
                watchlisted_player_ids = await filter_watchlisted_player_ids(
                    db,
                    player_ids=[player.player_id for player in db_report.players],
                    community_id=self.community_id,
                )

            responses = list(responses.values())
            view = await get_report_review_view(
                report=db_report,
                responses=responses,
                watchlisted_player_ids=watchlisted_player_ids,
                stats=stats,
            )

            if _original_interaction:
                if _original_interaction.message:
                    await _original_interaction.message.edit(
                        view=view, content=None, embed=None
                    )
                await _original_interaction.delete_original_response()
                await interaction.response.defer()
            else:
                await interaction.response.edit_message(
                    view=view, content=None, embed=None
                )

    async def refresh_report_view(self, interaction: Interaction):
        async with session_factory() as db:
            db_report = await get_report_by_id(db, self.report_id, load_token=True)
            if not db_report:
                raise CustomException(
                    f"Report with ID {self.report_id} no longer exists!"
                )
            report = schemas.ReportWithToken.model_validate(db_report)

            db_community = await get_community_by_id(db, self.community_id)
            community = schemas.Community.model_validate(db_community)
            stats = await bulk_get_response_stats(db, report.players)
            responses = await get_pending_responses(db, community, report.players)

            watchlisted_player_ids = await filter_watchlisted_player_ids(
                db,
                player_ids=[player.player_id for player in report.players],
                community_id=self.community_id,
            )

        # try:
        #     selected = [response.pr_id for response in responses].index(self.pr_id)
        # except ValueError:
        #     selected = 0

        view = await get_report_review_view(
            report=report,
            responses=responses,
            watchlisted_player_ids=watchlisted_player_ids,
            stats=stats,
        )
        await interaction.response.edit_message(content=None, embed=None, view=view)

    async def edit_comment(self, interaction: Interaction):
        async with session_factory() as db:
            db_report = await get_report_by_id(db, self.report_id)
            if not db_report:
                raise CustomException(
                    f"Report with ID {self.report_id} no longer exists!"
                )

            db_community = await get_community(db, self.community_id)
            community = schemas.Community.model_validate(db_community)
            assert isinstance(interaction.user, discord.Member)
            assert_has_admin_role(interaction.user, community, db_report.game)

        modal = ReportEditCommentModal(
            community_id=self.community_id,
            report_id=self.report_id,
            comment=db_report.comment,
        )
        await interaction.response.send_modal(modal)


class ReportEditCommentModal(Modal, title="Provide Additional Context"):
    def __init__(self, community_id: int, report_id: int, comment: str | None = None):
        super().__init__()
        self.community_id = community_id
        self.report_id = report_id

        self.comment_input = discord.ui.TextInput(
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=4000,
            default=comment,
        )

        self.add_item(
            discord.ui.Label(
                text="Additional Context",
                description="Provide any additional context that may help other communities make a better informed decision.",
                component=self.comment_input,
            )
        )
        self.add_item(
            discord.ui.TextDisplay(
                "-# ⚠️  **Provide context only.**\n-# Give each community the opportunity to draw their own conclusions. All provided information must be factual."
            )
        )

    async def on_submit(self, interaction: Interaction):
        comment = self.comment_input.value.strip()

        async with session_factory() as db:
            db_report = await set_report_comment(db, self.report_id, comment=comment)
            report = schemas.ReportWithToken.model_validate(db_report)

            db_community = await get_community_by_id(db, self.community_id)
            community = schemas.Community.model_validate(db_community)
            assert isinstance(interaction.user, discord.Member)
            assert_has_admin_role(interaction.user, community, db_report.game)

            stats = await bulk_get_response_stats(db, report.players)
            responses = await get_pending_responses(db, community, report.players)

            watchlisted_player_ids = await filter_watchlisted_player_ids(
                db,
                player_ids=[player.player_id for player in report.players],
                community_id=self.community_id,
            )

        view = await get_report_review_view(
            report=report,
            responses=responses,
            watchlisted_player_ids=watchlisted_player_ids,
            stats=stats,
        )
        await interaction.response.edit_message(content=None, embed=None, view=view)


def report_review_view_action_row_factory(
    player: schemas.PlayerReportRef,
    response: schemas.PendingResponse | None,
    *,
    watchlisted_player_ids: set[str],
) -> discord.ui.ActionRow | None:
    if not response:
        return None

    action_row = discord.ui.ActionRow()

    if response.banned is True:
        action_row.add_item(
            ReportReviewButton(
                button=discord.ui.Button(
                    label="Unban player",
                    style=ButtonStyle.blurple,
                    disabled=False,
                    row=1,
                ),
                command="unban",
                community_id=response.community_id,
                report_id=response.player_report.report_id,
                pr_id=response.pr_id,
            )
        )
    else:
        action_row.add_item(
            ReportReviewButton(
                button=discord.ui.Button(
                    label="Ban player",
                    style=ButtonStyle.red
                    if response.banned is None
                    else ButtonStyle.gray,
                    disabled=False,
                    row=1,
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
        elif response.reject_reason == reason:
            button_style = ButtonStyle.green
        else:
            button_style = ButtonStyle.gray

        if reason == ReportRejectReason.INCONCLUSIVE:
            disabled = response.banned is False
        else:
            disabled = (
                response.banned is False
                and response.reject_reason != ReportRejectReason.INCONCLUSIVE
            )

        action_row.add_item(
            ReportReviewButton(
                button=discord.ui.Button(
                    label=reason.value, style=button_style, disabled=disabled, row=1
                ),
                command="reject",
                community_id=response.community_id,
                report_id=response.player_report.report_id,
                pr_id=response.pr_id,
                reject_reason=reason,
            )
        )

    if response.banned is False:
        is_watchlisted = response.player_report.player_id in watchlisted_player_ids
        action_row.add_item(
            PlayerToggleWatchlistButton.create(
                community_id=response.community_id,
                player_id=response.player_report.player_id,
                is_watchlisted=is_watchlisted,
                row=2,
            )
        )

    return action_row


async def get_report_review_view(
    report: schemas.ReportWithToken,
    responses: list[schemas.PendingResponse],
    watchlisted_player_ids: set[str],
    stats: dict[int, schemas.ResponseStats] | None = None,
    with_refresh_button: bool = True,
) -> LayoutView:
    if not responses:
        raise ValueError("Must have at least one response")

    # Get container color
    container_color = discord.Colour.dark_theme()
    if any(response.banned is None for response in responses):
        container_color = discord.Colour.blurple()
    elif any(response.banned is True for response in responses):
        container_color = discord.Colour(0x521616)  # dark red
    elif all(response.banned is False for response in responses):
        container_color = discord.Colour(0x253021)  # dark green

    # Create view
    view = await get_plain_report_view(
        report,
        responses=responses,
        stats=stats,
        with_comment=True,
        container_color=container_color,
        player_action_row_factory=functools.partial(
            report_review_view_action_row_factory,
            watchlisted_player_ids=watchlisted_player_ids,
        ),
        refresh_button=ReportReviewButton(
            button=discord.ui.Button(emoji=Emojis.REFRESH, style=ButtonStyle.gray),
            command="refresh",
            community_id=responses[0].community_id,
            report_id=report.id,
            pr_id=responses[0].pr_id,
        )
        if with_refresh_button
        else None,
        comment_button=ReportReviewButton(
            button=discord.ui.Button(
                emoji=Emojis.WRITE if report.comment else Emojis.COMMENT,
                style=ButtonStyle.gray,
            ),
            command="comment",
            community_id=responses[0].community_id,
            report_id=report.id,
            pr_id=responses[0].pr_id,
        ),
    )

    return view
