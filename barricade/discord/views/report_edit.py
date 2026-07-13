import re
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from typing import Any

import discord

from barricade import schemas
from barricade.constants import REPORT_MAX_ATTACHMENTS, REPORT_MAX_PLAYERS
from barricade.crud.reports import edit_report, get_report_by_id
from barricade.db import session_factory
from barricade.discord.communities import assert_has_admin_role
from barricade.discord.utils import (
    CallableButton,
    CustomException,
    LayoutView,
    Modal,
    get_success_container,
)
from barricade.discord.views.report import (
    container_add_attachments,
    container_add_description,
    container_add_player,
    container_add_reasons,
    get_game_pill,
    get_platform_pill,
    get_player_avatar_urls,
)
from barricade.enums import (
    Game,
    Platform,
    PlatformFlag,
    PlayerIDType,
    PlayerPlatform,
    ReportReasonDetails,
    ReportReasonFlag,
)
from barricade.utils import get_player_id_type, validate_url

RE_BM_RCON_PLAYER_URL = re.compile(
    r"^https://www\.battlemetrics\.com/rcon/players/(\d+)$"
)


class ReportEditViewParams(schemas.ReportEditParams):
    players: list[schemas.PlayerReportCreateParams]  # Remove min_length requirement


class ReportValidationError(Exception):
    pass


def _get_assertion_result(assertion_func: Callable[[], Any]) -> bool:
    try:
        assertion_func()
    except ReportValidationError:
        return False
    return True


class _ReportEditView(LayoutView):
    def __init__(self):
        super().__init__(timeout=60 * 30)  # 30 minutes

        self.params = ReportEditViewParams(
            body="",
            reasons_bitflag=ReportReasonFlag(0),
            reasons_custom=None,
            game=Game.HLL,
            platforms_bitflag=PlatformFlag(0),
            players=[],
            created_at=datetime.now(UTC),
            edited_at=None,
            edited_by=None,
        )

    def _assert_valid_tags(self) -> None:
        if self.params.game is None:
            raise ReportValidationError("No game selected.")
        if self.params.platforms_bitflag == 0:
            raise ReportValidationError("No platforms selected.")

        if (
            self.params.game == Game.HLL
            and self.params.platforms_bitflag == PlatformFlag.all()
        ):
            raise ReportValidationError(
                "HLL servers do not support crossplay between PC and consoles."
            )

    def _assert_valid_reasons(self) -> None:
        if self.params.reasons_bitflag == 0:
            raise ReportValidationError("No reasons provided.")

    def _assert_valid_description(self) -> None:
        if not self.params.body.strip():
            raise ReportValidationError("No description provided.")

        if "imgur.com" in self.params.body:
            raise ReportValidationError("Please do not use Imgur links.")
        if "streamable.com" in self.params.body:
            raise ReportValidationError("Please do not use Streamable links.")

    def _assert_valid_player(self, player_index: int) -> None:
        if player_index < 0 or player_index >= len(self.params.players):
            raise ValueError(
                f"Invalid player index {player_index} (len={len(self.params.players)})"
            )

        player = self.params.players[player_index]
        rank = player_index + 1

        if not player.player_name.strip():
            raise ReportValidationError(f"Player {rank} name cannot be empty")
        if not player.player_id.strip():
            raise ReportValidationError(f"Player {rank} must have a player ID")

        try:
            player_id_type = get_player_id_type(player.player_id)
        except ValueError:
            raise ReportValidationError(
                f"Player {rank} has an invalid player ID"
            ) from None

        if player.platform:
            if not player.platform.is_valid_for_platform_flag(
                self.params.platforms_bitflag
            ):
                raise ReportValidationError(
                    f"Player {rank} platform contradicts crossplay settings"
                )

            if (player.platform == PlayerPlatform.STEAM) and (
                player_id_type != PlayerIDType.STEAM_64_ID
            ):
                raise ReportValidationError(f"Player {rank} requires a Steam64ID")

            if (player.platform != PlayerPlatform.STEAM) and (
                player_id_type == PlayerIDType.STEAM_64_ID
            ):
                raise ReportValidationError(
                    f"Player {rank} is not a Steam player but has a Steam64ID"
                )

        if (
            player.bm_rcon_url
            and RE_BM_RCON_PLAYER_URL.match(player.bm_rcon_url) is None
        ):
            raise ReportValidationError(
                f"Player {rank} has invalid Battlemetrics RCON URL"
            )

    def _assert_valid_players(self) -> None:
        if len(self.params.players) == 0:
            raise ReportValidationError("No players provided")
        if len(self.params.players) > REPORT_MAX_PLAYERS:
            raise ReportValidationError(
                f"Too many players provided (>{REPORT_MAX_PLAYERS})"
            )

        for i in range(len(self.params.players)):
            self._assert_valid_player(i)

    def validate_params(self) -> tuple[bool, str | None]:
        try:
            self._assert_valid_tags()
            self._assert_valid_reasons()
            self._assert_valid_description()
            self._assert_valid_players()
        except ReportValidationError as e:
            return False, str(e)

        return True, "Ready to submit report!"

    def has_valid_tags(self) -> bool:
        return _get_assertion_result(self._assert_valid_tags)

    def has_valid_reasons(self) -> bool:
        return _get_assertion_result(self._assert_valid_reasons)

    def has_valid_description(self) -> bool:
        return _get_assertion_result(self._assert_valid_description)

    def has_valid_players(self) -> bool:
        return _get_assertion_result(self._assert_valid_players)

    def is_valid_player(self, player_index: int) -> bool:
        return _get_assertion_result(lambda: self._assert_valid_player(player_index))

    async def update_view(self) -> None:
        self.clear_items()

        container = discord.ui.Container()

        container_add_reasons(container, self.params)
        container.add_item(
            discord.ui.ActionRow(
                CallableButton(
                    self.open_reasons_modal,
                    label=("Edit" if self.params.reasons_bitflag else "Add reason"),
                    style=(
                        discord.ButtonStyle.gray
                        if self.has_valid_reasons()
                        else discord.ButtonStyle.blurple
                    ),
                )
            )
        )

        container.add_item(
            discord.ui.Separator(visible=False, spacing=discord.SeparatorSpacing.small)
        )

        container_add_description(container, self.params)
        container.add_item(
            discord.ui.ActionRow(
                CallableButton(
                    self.open_description_modal,
                    label=("Edit" if self.params.body.strip() else "Add description"),
                    style=(
                        discord.ButtonStyle.gray
                        if self.has_valid_description()
                        else discord.ButtonStyle.blurple
                    ),
                )
            )
        )

        # Add attachments
        container_add_attachments(container, self.params)

        if self.params.attachment_urls:
            action_row = discord.ui.ActionRow()
            container.add_item(action_row)

            if len(self.params.attachment_urls) == 1:
                action_row.add_item(
                    CallableButton(
                        partial(self.remove_attachment, index=0),
                        label="Remove",
                        style=discord.ButtonStyle.red,
                    )
                )

            else:
                for i in range(
                    min(len(self.params.attachment_urls), REPORT_MAX_ATTACHMENTS)
                ):
                    if i % 5 == 0 and i > 0:
                        action_row = discord.ui.ActionRow()
                        container.add_item(action_row)

                    action_row.add_item(
                        CallableButton(
                            partial(self.remove_attachment, index=i),
                            label=f"Remove #{i + 1}",
                            style=discord.ButtonStyle.red,
                        )
                    )

        player_avatar_urls = await get_player_avatar_urls(self.params.players)

        # Reported player(s)
        for i, player in enumerate(self.params.players):
            container_add_player(
                container,
                self.params,
                player,
                rank=i + 1,
                avatar_url=player_avatar_urls[i],
            )
            container.add_item(
                discord.ui.ActionRow(
                    CallableButton(
                        partial(self.open_player_modal, player_index=i),
                        label="Edit",
                        style=(
                            discord.ButtonStyle.gray
                            if self.is_valid_player(i)
                            else discord.ButtonStyle.blurple
                        ),
                    ),
                    CallableButton(
                        partial(self.remove_player, player_index=i),
                        label="Remove",
                        style=discord.ButtonStyle.red,
                    ),
                )
            )

        has_player = len(self.params.players) > 0
        container.add_item(
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large)
        )
        container.add_item(
            discord.ui.ActionRow(
                CallableButton(
                    partial(self.open_player_modal, player_index=None),
                    label=("Add another player" if has_player else "Add a player"),
                    style=(
                        discord.ButtonStyle.gray
                        if has_player
                        else discord.ButtonStyle.blurple
                    ),
                    disabled=len(self.params.players) >= REPORT_MAX_PLAYERS,
                ),
                CallableButton(
                    self.open_attachments_modal,
                    label="Upload attachments",
                    style=discord.ButtonStyle.gray,
                    disabled=len(self.params.attachment_urls) >= REPORT_MAX_ATTACHMENTS,
                ),
            )
        )

        self.add_item(container)

        tags = f"-# {get_game_pill(self.params.game or Game.HLL)} {get_platform_pill(self.params.platforms_bitflag)}"
        self.add_item(
            discord.ui.Section(
                discord.ui.TextDisplay(tags),
                accessory=CallableButton(
                    partial(self.open_tags_modal),
                    label="Change Tags",
                    style=(
                        discord.ButtonStyle.gray
                        if self.has_valid_tags()
                        else discord.ButtonStyle.blurple
                    ),
                ),
            )
        )

        is_ready, progress = self.validate_params()
        container = discord.ui.Container(
            accent_color=discord.Colour.brand_green()
            if is_ready
            else discord.Colour.red()
        )
        container.add_item(
            discord.ui.Section(
                discord.ui.TextDisplay(
                    f"## {progress}"
                    if is_ready
                    else f"**Complete the report before submitting.**\n-# {progress}"
                ),
                accessory=CallableButton(
                    self.submit_report,
                    label="Submit Report",
                    style=discord.ButtonStyle.green,
                    disabled=not is_ready,
                ),
            )
        )

        self.add_item(container)

    async def open_tags_modal(self, interaction: discord.Interaction):
        modal = ReportEditTagsModal(self)
        await interaction.response.send_modal(modal)

    async def open_reasons_modal(self, interaction: discord.Interaction):
        modal = ReportEditReasonsModal(self)
        await interaction.response.send_modal(modal)

    async def open_description_modal(self, interaction: discord.Interaction):
        modal = ReportEditDescriptionModal(self)
        await interaction.response.send_modal(modal)

    async def open_attachments_modal(self, interaction: discord.Interaction):
        modal = ReportUploadAttachmentsModal(self)
        await interaction.response.send_modal(modal)

    async def remove_attachment(self, interaction: discord.Interaction, *, index: int):
        del self.params.attachment_urls[index]
        await self.update_view()
        await interaction.response.edit_message(view=self)

    async def open_player_modal(
        self, interaction: discord.Interaction, *, player_index: int | None
    ):
        player = None if player_index is None else self.params.players[player_index]
        modal = ReportEditPlayerModal(self, player)
        await interaction.response.send_modal(modal)

    async def remove_player(
        self, interaction: discord.Interaction, *, player_index: int
    ):
        del self.params.players[player_index]
        await self.update_view()
        await interaction.response.edit_message(view=self)

    async def submit_report(self, interaction: discord.Interaction):
        raise NotImplementedError


class ReportEditView(_ReportEditView):
    def __init__(self, report_id: int):
        super().__init__()
        self.report_id = report_id

    @classmethod
    async def from_report(cls, report: schemas.ReportWithToken):
        view = cls(report_id=report.id)
        view.params = ReportEditViewParams(
            body=report.body,
            reasons_bitflag=report.reasons_bitflag,
            reasons_custom=report.reasons_custom,
            game=report.game,
            platforms_bitflag=report.platforms_bitflag,
            players=[
                schemas.PlayerReportCreateParams(
                    player_id=player.player_id,
                    player_name=player.player_name,
                    platform=player.player.platform,
                    bm_rcon_url=player.player.bm_rcon_url,
                )
                for player in report.players
            ],
            created_at=report.created_at,
            edited_at=report.edited_at,
            edited_by=report.edited_by,
        )
        await view.update_view()
        return view

    async def submit_report(self, interaction: discord.Interaction):
        async with session_factory.begin() as db:
            # Load all relations now, they will be used by edit_report later
            db_report = await get_report_by_id(db, self.report_id, load_relations=True)
            if not db_report:
                raise CustomException("Report not found!")
            report = schemas.ReportWithToken.model_validate(db_report)

            if interaction.user.id != report.token.admin_id:
                assert isinstance(interaction.user, discord.Member)
                assert_has_admin_role(
                    interaction.user, report.token.community, report.game
                )

            params = schemas.ReportCreateParams(
                **self.params.model_dump(exclude={"edited_at", "edited_by"}),
                token_id=report.token.id,
                edited_at=datetime.now(UTC),
                edited_by=interaction.user.mention,
            )

            await interaction.response.defer(ephemeral=True)

            await edit_report(
                db,
                report=params,
                by=interaction.user.name,
            )

            view = discord.ui.LayoutView()
            view.add_item(get_success_container("Report updated!"))
            await interaction.edit_original_response(embed=None, view=view)


class ReportEditTagsModal(Modal):
    def __init__(self, view: _ReportEditView, *, send_on_submit: bool = False):
        super().__init__(
            title="Create Report" if send_on_submit else "Edit Report Tags"
        )
        self.view = view
        self.send_on_submit = send_on_submit

        self.game_input = discord.ui.RadioGroup(
            options=[
                discord.RadioGroupOption(
                    label="🌲 Hell Let Loose",
                    value=Game.HLL.name,
                    default=view.params.game == Game.HLL and not send_on_submit,
                ),
                discord.RadioGroupOption(
                    label="🌴 HLL: Vietnam",
                    value=Game.HLLV.name,
                    default=view.params.game == Game.HLLV,
                ),
            ],
        )

        self.platforms_input = discord.ui.CheckboxGroup(
            min_values=1,
            options=[
                discord.CheckboxGroupOption(
                    label=platform.value,
                    value=platform.name,
                    default=(view.params.platforms_bitflag & platform.to_flag() != 0),
                )
                for platform in Platform
            ],
        )

        self.add_item(
            discord.ui.Label(
                text="Game",
                description="Which game is this report for?",
                component=self.game_input,
            )
        )

        self.add_item(
            discord.ui.Label(
                text="Crossplay",
                description="Which platforms are able to join your server(s)?",
                component=self.platforms_input,
            )
        )

    def get_game(self) -> Game:
        if not self.game_input.value:
            raise ValueError("No game selected")
        return Game[self.game_input.value]

    def get_platforms(self) -> PlatformFlag:
        platforms_bitflag = PlatformFlag(0)
        for platform_name in self.platforms_input.values:
            platforms_bitflag |= Platform[platform_name].to_flag()
        return platforms_bitflag

    async def on_submit(self, interaction: discord.Interaction):
        game = self.get_game()
        platforms_bitflag = self.get_platforms()

        self.view.params.game = game
        self.view.params.platforms_bitflag = platforms_bitflag
        await self.view.update_view()

        if self.send_on_submit:
            await interaction.response.send_message(view=self.view, ephemeral=True)
        else:
            await interaction.response.edit_message(view=self.view)


class ReportEditReasonsModal(Modal):
    def __init__(self, view: _ReportEditView):
        super().__init__(title="Edit Report Description")
        self.view = view

        self.reasons_input = discord.ui.CheckboxGroup(
            options=[
                discord.CheckboxGroupOption(
                    label=f"{reason.value.emoji} {reason.value.pretty_name}",
                    value=reason.name,
                    default=(view.params.reasons_bitflag & reason.to_flag() != 0),
                )
                for reason in ReportReasonDetails
            ],
        )

        self.add_item(
            discord.ui.Label(
                text="Reasons",
                description="Select the reason(s) for this report.",
                component=self.reasons_input,
            )
        )

    def get_reasons(self) -> tuple[ReportReasonFlag, str | None]:
        reasons_bitflag = ReportReasonFlag(0)
        for reason in self.reasons_input.values:
            reasons_bitflag |= ReportReasonFlag[reason]

        # TODO: Allow for custom reason
        return reasons_bitflag, None

    async def on_submit(self, interaction: discord.Interaction):
        reasons_bitflag, custom_msg = self.get_reasons()
        self.view.params.reasons_bitflag = reasons_bitflag
        self.view.params.reasons_custom = custom_msg
        await self.view.update_view()
        await interaction.response.edit_message(view=self.view)


class ReportEditDescriptionModal(Modal):
    def __init__(self, view: _ReportEditView):
        super().__init__(title="Edit Report Description")
        self.view = view

        self.description_input = discord.ui.TextInput(
            style=discord.TextStyle.paragraph,
            placeholder="Substantiate your report...",
            default=view.params.body,
            required=True,
            max_length=3950,
        )

        self.add_item(
            discord.ui.Label(
                text="Description",
                description=(
                    "Explain the situation and provide evidence. Markdown is supported."
                ),
                component=self.description_input,
            )
        )

    def get_description(self) -> str:
        return self.description_input.value.strip()

    async def on_submit(self, interaction: discord.Interaction):
        self.view.params.body = self.get_description()
        await self.view.update_view()
        await interaction.response.edit_message(view=self.view)


class ReportUploadAttachmentsModal(Modal):
    def __init__(self, view: _ReportEditView):
        super().__init__(title="Upload Media")
        self.view = view

        self.attachments_input = discord.ui.FileUpload(
            required=False,
            min_values=0,
            max_values=REPORT_MAX_ATTACHMENTS - len(self.view.params.attachment_urls),
        )

        self.add_item(
            discord.ui.Label(
                text="Media",
                description="Images and videos only. Any other file types will be ignored.",
                component=self.attachments_input,
            )
        )

    def get_attachments(self) -> list[discord.Attachment]:
        if (
            len(self.view.params.attachment_urls) + len(self.attachments_input.values)
            > REPORT_MAX_ATTACHMENTS
        ):
            raise CustomException(
                "Too many attachments provided",
                f"Reports can have at most {REPORT_MAX_ATTACHMENTS} attachments.",
            )

        return [
            attachment
            for attachment in self.attachments_input.values
            if attachment.content_type
            and attachment.content_type.startswith(("image/", "video/"))
        ]

    async def on_submit(self, interaction: discord.Interaction):
        for attachment in self.get_attachments():
            self.view.params.attachment_urls.append(attachment.url)
        await self.view.update_view()
        await interaction.response.edit_message(view=self.view)


class ReportEditPlayerModal(Modal):
    def __init__(
        self,
        view: _ReportEditView,
        player: schemas.PlayerReportCreateParams | None,
    ):
        super().__init__(title="Edit Player")
        self.view = view
        self.player = player

        self.player_name_input = discord.ui.TextInput(
            style=discord.TextStyle.short,
            default=player.player_name if player else None,
            required=True,
            max_length=32,
        )

        self.player_id_input = discord.ui.TextInput(
            style=discord.TextStyle.short,
            default=player.player_id if player else None,
            required=True,
            min_length=17,
            max_length=32,
        )

        self.platform_input = discord.ui.RadioGroup(
            required=False,
            options=[
                discord.RadioGroupOption(
                    label=platform.value,
                    value=platform.name,
                    default=(player.platform == platform if player else False),
                )
                for platform in PlayerPlatform
                if platform.is_valid_for_platform_flag(
                    self.view.params.platforms_bitflag
                )
            ],
        )

        self.bm_rcon_url_input = discord.ui.TextInput(
            style=discord.TextStyle.short,
            placeholder="https://www.battlemetrics.com/rcon/players/...",
            default=player.bm_rcon_url if player else None,
            required=False,
            min_length=43,
        )

        self.add_item(
            discord.ui.Label(
                text="Player Name",
                description="The player's in-game name.",
                component=self.player_name_input,
            )
        )

        self.add_item(
            discord.ui.Label(
                text="Player ID",
                description="The player's unique ID (Steam64ID or Team17 ID).",
                component=self.player_id_input,
            )
        )

        # self.add_item(discord.ui.Separator())

        self.add_item(
            discord.ui.Label(
                text="Platform",
                description="The platform the player is on. Leave empty if unsure.",
                component=self.platform_input,
            )
        )

        self.add_item(
            discord.ui.TextDisplay(
                "-# If the player is on Steam, their ID must be a Steam64ID."
            )
        )

        self.add_item(
            discord.ui.Label(
                text="Battlemetrics RCON URL",
                description="A URL to the player's Battlemetrics RCON page.",
                component=self.bm_rcon_url_input,
            )
        )

    def get_player_name(self) -> str:
        return self.player_name_input.value.strip()

    def get_player_id(self) -> str:
        return self.player_id_input.value.strip()

    def get_platform(self) -> PlayerPlatform | None:
        if not self.platform_input.value:
            try:
                player_id_type = get_player_id_type(self.player_id_input.value.strip())
            except ValueError:
                return None

            if player_id_type == PlayerIDType.STEAM_64_ID:
                return PlayerPlatform.STEAM

            return None
        return PlayerPlatform[self.platform_input.value]

    def get_bm_rcon_url(self) -> str | None:
        url = self.bm_rcon_url_input.value.strip()
        if not url:
            return None

        try:
            return validate_url(self.bm_rcon_url_input.value, strict=True)
        except ValueError:
            return url

    async def on_submit(self, interaction: discord.Interaction):
        if self.player:
            self.player.player_name = self.get_player_name()
            self.player.player_id = self.get_player_id()
            self.player.platform = self.get_platform()
            self.player.bm_rcon_url = self.get_bm_rcon_url()
        else:
            player = schemas.PlayerReportCreateParams(
                player_name=self.get_player_name(),
                player_id=self.get_player_id(),
                platform=self.get_platform(),
                bm_rcon_url=self.get_bm_rcon_url(),
            )
            self.view.params.players.append(player)

        await self.view.update_view()
        await interaction.response.edit_message(view=self.view)
