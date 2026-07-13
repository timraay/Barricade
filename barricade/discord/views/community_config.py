import re
from collections.abc import Callable
from functools import partial
from typing import Generic, TypeVar, assert_never

import discord
from discord.utils import escape_markdown as esc_md
from sqlalchemy.ext.asyncio import AsyncSession

from barricade import schemas
from barricade.config import (
    CONFIG_OPTIONS,
    ConfigOption,
    ConfigOptionCategory,
    ConfigOptionType,
)
from barricade.crud.communities import edit_community
from barricade.db import models, session_factory
from barricade.discord.bot import bot
from barricade.discord.communities import (
    assert_has_any_admin_role,
    get_text_channel,
)
from barricade.discord.crud_utils import get_community
from barricade.discord.utils import (
    CustomException,
    LayoutView,
    Modal,
    get_command_mention,
    handle_error_wrap,
)
from barricade.enums import (
    GameFlag,
    PlatformFlag,
    ReportReasonDetails,
    ReportReasonFlag,
)

T = TypeVar("T")


class CommunityConfigCategoryButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"community:(?P<community_id>\d+):config:category:(?P<category>\w+)",
):
    def __init__(
        self,
        community_id: int,
        category: ConfigOptionCategory,
        button: discord.ui.Button | None = None,
    ):
        self.community_id = community_id
        self.category = category

        if not button:
            button = discord.ui.Button(
                style=discord.ButtonStyle.gray,
                label=self.category.value,
            )
        button.custom_id = (
            f"community:{self.community_id}:config:category:{self.category.name}"
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
        return cls(
            community_id=int(match["community_id"]),
            category=ConfigOptionCategory[match["category"]],
            button=item,
        )

    @handle_error_wrap
    async def callback(self, interaction: discord.Interaction):
        async with session_factory() as db:
            db_community = await get_community(db, self.community_id)
            community = schemas.Community.model_validate(db_community)
            assert isinstance(interaction.user, discord.Member)
            assert_has_any_admin_role(interaction.user, community)

        view = await get_community_config_view(community, self.category)
        await interaction.response.edit_message(view=view)


class CommunityConfigEditButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"community:(?P<community_id>\d+):config:edit:(?P<option_id>\w+)",
):
    def __init__(
        self,
        community_id: int,
        option: ConfigOption,
        button: discord.ui.Button | None = None,
    ):
        self.community_id = community_id
        self.option = option

        if not button:
            button = discord.ui.Button(
                style=discord.ButtonStyle.blurple,
                label="Edit",
            )
        button.custom_id = f"community:{self.community_id}:config:edit:{self.option.id}"
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
            community_id=int(match["community_id"]),
            option=CONFIG_OPTIONS[match["option_id"]],
            button=item,
        )

    @handle_error_wrap
    async def callback(self, interaction: discord.Interaction):
        async with session_factory() as db:
            db_community = await get_community(db, self.community_id)
            community = schemas.Community.model_validate(db_community)
            assert isinstance(interaction.user, discord.Member)
            assert_has_any_admin_role(interaction.user, community)

        modal = get_community_config_edit_modal(community, self.option)
        await modal.assert_is_allowed_in_guild(interaction.guild)
        await interaction.response.send_modal(modal)


class CommunityConfigView(LayoutView):
    def __init__(
        self,
        community: schemas.Community,
        category: ConfigOptionCategory,
    ):
        super().__init__(timeout=None)
        self.community = community
        self.category = category
        self.setup_view()

    def setup_view(self) -> None:
        self.clear_items()
        self.add_item(self._get_category_select_container())
        self.add_item(self._get_main_container())

    def _get_category_select_container(self) -> discord.ui.Container:
        # Create action row
        action_row = discord.ui.ActionRow()
        for category in ConfigOptionCategory:
            button = CommunityConfigCategoryButton(self.community.id, category)
            if category == self.category:
                button.item.disabled = True
            action_row.add_item(button.item)

        # Add action row to container
        container = discord.ui.Container()
        container.add_item(action_row)

        return container

    def _get_main_container(self) -> discord.ui.Container:
        container = discord.ui.Container()
        container.add_item(discord.ui.TextDisplay(f"## {self.category.value}"))

        is_first_option = True
        for option in CONFIG_OPTIONS.values():
            if option.category != self.category:
                continue

            container.add_item(
                discord.ui.Separator(
                    visible=not is_first_option,
                    spacing=discord.SeparatorSpacing.small,
                )
            )
            is_first_option = False

            title_display = discord.ui.TextDisplay(
                f"**{option.name}**\n*{option.description}*"
            )
            container.add_item(title_display)

            value_strings = option_values_to_string(self.community, option).split(
                "\n\n"
            )
            value_displays = [
                discord.ui.TextDisplay(value_string) for value_string in value_strings
            ]
            container.add_item(
                discord.ui.Section(
                    *value_displays,
                    accessory=CommunityConfigEditButton(self.community.id, option),  # type: ignore
                )
            )

        return container


async def get_community_config_view(
    community: schemas.Community,
    category: ConfigOptionCategory = ConfigOptionCategory.GAMES,
) -> LayoutView:
    if category == ConfigOptionCategory.INTEGRATIONS:
        from barricade.discord.views.integration_config import IntegrationConfigView

        view = IntegrationConfigView(community)
        await view.prepare()
        return view

    return CommunityConfigView(community, category)


def quote_block(value: str) -> str:
    # Start each line with "> "
    return "> " + "\n> ".join(value.split("\n"))


def combine_option_value_strings(
    value_to_string_func: Callable[[T | None], str],
    community: schemas.CommunityRef,
    value1: T | None,
    value2: T | None,
    can_inherit_from: str | None = None,
    multiline: bool = True,
) -> str:
    if value1 is None and value2 is None and can_inherit_from:
        # Inherit from parent
        return f"-# > Same as **{can_inherit_from}**"

    display1 = value_to_string_func(value1)
    display2 = value_to_string_func(value2)

    if value1 == value2:
        # value is equal for both games
        return f">>> {display1}"

    if community.games_bitflag == GameFlag.HLL:
        # Only HLL is enabled, show only the first value
        return f">>> {display1}"

    if community.games_bitflag == GameFlag.HLLV:
        # Only HLLV is enabled, show only the second value
        return f">>> {display2}"

    if multiline:
        return f"-# **HLL (WWII)**\n{quote_block(display1)}\n\n-# **HLL: Vietnam**\n{quote_block(display2)}"
    return f"-# **HLL (WWII)**  |  **HLL: Vietnam**\n> {display1} {display2}"


def _text_channel_value_to_string(
    community: schemas.CommunityRef, value: int | None
) -> str:
    channel = get_text_channel(community.guild_id, value)
    if channel:
        return channel.mention
    elif value:
        return "Unknown"
    return "None"


def text_channel_values_to_string(
    community: schemas.CommunityRef,
    value1: int | None,
    value2: int | None,
    can_inherit_from: str | None = None,
) -> str:
    return combine_option_value_strings(
        partial(_text_channel_value_to_string, community),
        community,
        value1,
        value2,
        can_inherit_from=can_inherit_from,
    )


def _role_value_to_string(value: int | None) -> str:
    return f"<@&{value}>" if value else "None"


def role_values_to_string(
    community: schemas.CommunityRef,
    value1: int | None,
    value2: int | None,
    can_inherit_from: str | None = None,
) -> str:
    return combine_option_value_strings(
        _role_value_to_string,
        community,
        value1,
        value2,
        can_inherit_from=can_inherit_from,
    )


def _game_filter_value_to_string(value: GameFlag | None) -> str:
    hll_str = "🌲 **Hell Let Loose** (2021)"
    hllv_str = "🌴 **Hell Let Loose: Vietnam** (2026)"

    if value is None or value == GameFlag.all():
        return f"{hll_str}\n{hllv_str}"

    match value:
        case GameFlag.HLL:
            return hll_str
        case GameFlag.HLLV:
            return hllv_str
        case GameFlag(0):
            return "Nothing"
        case _:
            raise ValueError(f"Unexpected game: {value}")


def game_filter_values_to_string(
    community: schemas.CommunityRef,
    value1: GameFlag | None,
    value2: GameFlag | None,
    can_inherit_from: str | None = None,
) -> str:
    return combine_option_value_strings(
        _game_filter_value_to_string,
        community,
        value1,
        value2,
        multiline=True,
        can_inherit_from=can_inherit_from,
    )


def _platform_filter_value_to_string(value: PlatformFlag | None) -> str:
    if value is None or value == PlatformFlag.all():
        return "PC & Console"

    match value:
        case PlatformFlag.PC:
            return "PC only"
        case PlatformFlag.CONSOLE:
            return "Console only"
        case PlatformFlag(0):
            return "Nothing"
        case _:
            raise ValueError(f"Unexpected platform: {value}")


def platform_filter_values_to_string(
    community: schemas.CommunityRef,
    value1: PlatformFlag | None,
    value2: PlatformFlag | None,
    can_inherit_from: str | None = None,
) -> str:
    return combine_option_value_strings(
        _platform_filter_value_to_string,
        community,
        value1,
        value2,
        multiline=True,
        can_inherit_from=can_inherit_from,
    )


def _reason_filter_value_to_string(value: ReportReasonFlag | None) -> str:
    if not value or value == ReportReasonFlag.all():
        return "All"
    elif value == 0:
        return "None"
    return "\n".join(value.to_list(custom_msg="Custom", with_emoji=True))


def reason_filter_values_to_string(
    community: schemas.CommunityRef,
    value1: ReportReasonFlag | None,
    value2: ReportReasonFlag | None,
    can_inherit_from: str | None = None,
) -> str:
    return combine_option_value_strings(
        _reason_filter_value_to_string,
        community,
        value1,
        value2,
        multiline=True,
        can_inherit_from=can_inherit_from,
    )


def option_values_to_string(
    community: schemas.CommunityRef,
    option: ConfigOption,
) -> str:
    value1, value2 = option.get_values(community)
    match option.type:
        case ConfigOptionType.TEXT_CHANNEL:
            assert isinstance(value1, int | None)
            assert isinstance(value2, int | None)
            return text_channel_values_to_string(
                community, value1, value2, can_inherit_from=option.can_inherit_from
            )
        case ConfigOptionType.ROLE:
            assert isinstance(value1, int | None)
            assert isinstance(value2, int | None)
            return role_values_to_string(
                community, value1, value2, can_inherit_from=option.can_inherit_from
            )
        case ConfigOptionType.GAME_FILTER:
            assert isinstance(value1, GameFlag | None)
            assert isinstance(value2, GameFlag | None)
            return game_filter_values_to_string(
                community, value1, value2, can_inherit_from=option.can_inherit_from
            )
        case ConfigOptionType.PLATFORM_FILTER:
            assert isinstance(value1, PlatformFlag | None)
            assert isinstance(value2, PlatformFlag | None)
            return platform_filter_values_to_string(
                community, value1, value2, can_inherit_from=option.can_inherit_from
            )
        case ConfigOptionType.REASON_FILTER:
            assert isinstance(value1, ReportReasonFlag | None)
            assert isinstance(value2, ReportReasonFlag | None)
            return reason_filter_values_to_string(
                community, value1, value2, can_inherit_from=option.can_inherit_from
            )
        case _:
            assert_never(option.type)
            raise ValueError(f"Unknown option type: {option.type}")


class _CommunityConfigEditModal(Generic[T], Modal):
    def __init__(
        self,
        community: schemas.Community,
        option: ConfigOption[T],
    ):
        super().__init__(title=f"Edit {option.name}")
        self.community = community
        self.option = option

        value1, value2 = self.option.get_values(self.community)

        self.games_bitflag = community.games_bitflag
        self.old_values = (value1, value2)

        self._is_split = self.can_split and value1 != value2

        self.setup_modal()

    def setup_modal(self) -> None:
        self.clear_items()

        self.add_item(
            discord.ui.TextDisplay(
                f"### {self.option.name}\n*{self.option.description}*"
            )
        )

        is_inheriting = self.is_inheriting
        is_split = self.is_split

        self.inherit_checkbox = discord.ui.Checkbox(default=is_inheriting)
        self.split_checkbox = discord.ui.Checkbox(default=is_split)
        self.setup_modal_components()

        component1, component2 = self.get_components()

        if not is_inheriting:
            if self.is_split:
                self.add_item(
                    discord.ui.Label(
                        text="HLL (WWII)",
                        component=component1,
                    )
                )
                self.add_item(
                    discord.ui.Label(
                        text="HLL: Vietnam",
                        component=component2,
                    )
                )
            else:
                self.add_item(
                    discord.ui.Label(
                        text="New value",
                        component=component1,
                    )
                )

        if self.can_split and not is_split:
            self.add_item(
                discord.ui.Label(
                    text="Show more options",
                    description="Add separate controls for HLL (WWII) and HLL: Vietnam",
                    component=self.split_checkbox,
                )
            )

        if self.can_inherit:
            self.add_item(
                discord.ui.Label(
                    text="Inherit values",
                    description=f"Use the same {'values' if self.option.is_game_dependent() else 'value'} as {self.option.can_inherit_from}",
                    component=self.inherit_checkbox,
                )
            )

    @property
    def can_inherit(self) -> bool:
        # Modal submit interactions cannot open a new model.
        # Setting this to false disables the controls that depend on this functionality.
        return False
        return self.option.can_inherit_from is not None

    @property
    def is_inheriting(self) -> bool:
        if not self.can_inherit:
            return False

        value1, value2 = self.option.get_values(self.community)
        return value1 is None and value2 is None

    @property
    def can_split(self) -> bool:
        return (
            self.option.is_game_dependent()
            and not self.is_inheriting
            and len(list(self.games_bitflag)) != 1
        )

    @property
    def is_split(self) -> bool:
        if not self.can_split:  # noqa: SIM103
            return False

        # Modal submit interactions cannot open a new model.
        # Setting this to false disables the controls that depend on this functionality.
        return True
        return self._is_split

    def setup_modal_components(self) -> None:
        raise NotImplementedError

    def get_components(self) -> tuple[discord.ui.Item, discord.ui.Item]:
        raise NotImplementedError

    def _get_values(
        self, raw_value1: T, raw_value2: T | None
    ) -> tuple[T | None, T | None]:
        if self.inherit_checkbox.value:
            return None, None

        if not self.option.is_game_dependent():
            return raw_value1, raw_value1

        if self.is_split:
            return raw_value1, raw_value2

        return raw_value1, raw_value1

    def get_values(self) -> tuple[T | None, T | None]:
        raise NotImplementedError

    async def refresh_community(self, db: AsyncSession) -> schemas.Community:
        db_community = await get_community(db, self.community.id)
        self.community = schemas.Community.model_validate(db_community)
        return self.community

    async def assert_is_allowed_in_guild(
        self, guild: discord.Guild | None, *, save: bool = False
    ):
        if not self.option.is_bound_to_guild:
            return

        if guild is None:
            raise CustomException(
                "Expected interaction to occur within a Discord server"
            )

        if self.community.guild_id is None:
            if save:
                self.community.guild_id = guild.id
            return

        if self.community.guild_id == guild.id:
            return

        migrate_command_mention = await get_command_mention(bot.tree, "migrate-guild")
        raise CustomException(
            "Community not bound to this Discord server",
            (
                "Each Barricade community can only be associated with one Discord server."
                "\n\n"
                "Please try again in the following server:"
                f"\n> {esc_md(guild.name)}"
                "\n\n"
                f"Want to use a different server? You can use {migrate_command_mention} to migrate."
            ),
        )

    async def save_community(self, db: AsyncSession):
        db_community = await db.get_one(models.Community, self.community.id)
        await edit_community(
            db, db_community, schemas.CommunityEditParams.model_validate(self.community)
        )

    async def on_submit(self, interaction: discord.Interaction):
        # was_inheriting = self.is_inheriting
        # was_split = self.is_split

        # Update community
        async with session_factory.begin() as db:
            await self.refresh_community(db)

            await self.assert_is_allowed_in_guild(interaction.guild, save=True)

            value1, value2 = self.get_values()

            # If the community only has one game enabled, do not override the value for the
            # disabled game, unless both are the same, in which case we keep them in sync.
            if self.old_values[0] != self.old_values[1]:
                if self.games_bitflag == GameFlag.HLL:
                    value2 = self.old_values[1]
                elif self.games_bitflag == GameFlag.HLLV:
                    value1 = self.old_values[0]

            self.option.set_values(self.community, value1, value2)

            await self.save_community(db)

        view = await get_community_config_view(self.community, self.option.category)

        # Modal submit interactions cannot open a new model.
        """
        if was_inheriting != self.is_inheriting:
            # If inheritance was toggled on/off, send a new modal
            self.setup_modal()
            await interaction.response.send_modal(self)
            # Also update the original message in case the user cancels the modal
            await interaction.edit_original_response(view=view)
        elif was_split is False and self.split_checkbox.value is True:
            # If split was toggled on, send a new modal
            self._is_split = True
            self.setup_modal()
            await interaction.response.send_modal(self)
            # Also update the original message in case the user cancels the modal
            await interaction.edit_original_response(view=view)
        else:
            # Otherwise refresh the config view
            await interaction.response.edit_message(view=view)
        """

        await interaction.response.edit_message(view=view)


class CommunityConfigEditTextChannelModal(_CommunityConfigEditModal[int]):
    def setup_modal_components(self) -> None:
        value1, value2 = self.option.get_values(self.community)

        self.select1 = discord.ui.ChannelSelect(
            channel_types=[discord.ChannelType.text],
            placeholder="No text channel selected",
            required=not self.option.is_nullable,
            default_values=[discord.Object(value1)] if value1 else [],
        )

        self.select2 = discord.ui.ChannelSelect(
            channel_types=[discord.ChannelType.text],
            placeholder="No text channel selected",
            required=not self.option.is_nullable,
            default_values=[discord.Object(value2)] if value2 else [],
        )

    def get_components(self) -> tuple[discord.ui.Item, discord.ui.Item]:
        return self.select1, self.select2

    def get_values(self) -> tuple[int | None, int | None]:
        raw_value1 = self.select1.values[0].id if self.select1.values else 0
        raw_value2 = self.select2.values[0].id if self.select2.values else 0
        return self._get_values(raw_value1, raw_value2)


class CommunityConfigEditRoleModal(_CommunityConfigEditModal[int | None]):
    def setup_modal_components(self) -> None:
        value1, value2 = self.option.get_values(self.community)

        self.select1 = discord.ui.RoleSelect(
            placeholder="No role selected",
            required=not self.option.is_nullable,
            default_values=[discord.Object(value1)] if value1 else [],
        )

        self.select2 = discord.ui.RoleSelect(
            placeholder="No role selected",
            required=not self.option.is_nullable,
            default_values=[discord.Object(value2)] if value2 else [],
        )

    def get_components(self) -> tuple[discord.ui.Item, discord.ui.Item]:
        return self.select1, self.select2

    def get_values(self) -> tuple[int | None, int | None]:
        raw_value1 = self.select1.values[0].id if self.select1.values else 0
        raw_value2 = self.select2.values[0].id if self.select2.values else 0
        return self._get_values(raw_value1, raw_value2)


class CommunityConfigEditGameFilterModal(_CommunityConfigEditModal[GameFlag | None]):
    def setup_modal_components(self) -> None:
        value1, value2 = self.option.get_values(self.community)

        self.checkbox_group1 = discord.ui.CheckboxGroup(
            required=True,
            min_values=1,
            options=[
                discord.CheckboxGroupOption(
                    label="🌲 Hell Let Loose (2021)",
                    value=str(GameFlag.HLL.value),
                    default=value1 is None or (value1 & GameFlag.HLL) != 0,
                ),
                discord.CheckboxGroupOption(
                    label="🌴 Hell Let Loose: Vietnam (2026)",
                    value=str(GameFlag.HLLV.value),
                    default=value1 is None or (value1 & GameFlag.HLLV) != 0,
                ),
            ],
        )

        self.checkbox_group2 = discord.ui.CheckboxGroup(
            required=True,
            min_values=1,
            options=[
                discord.CheckboxGroupOption(
                    label="🌲 Hell Let Loose (2021)",
                    value=str(GameFlag.HLL.value),
                    default=value2 is None or (value2 & GameFlag.HLL) != 0,
                ),
                discord.CheckboxGroupOption(
                    label="🌴 Hell Let Loose: Vietnam (2026)",
                    value=str(GameFlag.HLLV.value),
                    default=value2 is None or (value2 & GameFlag.HLLV) != 0,
                ),
            ],
        )

    def get_components(self) -> tuple[discord.ui.Item, discord.ui.Item]:
        return self.checkbox_group1, self.checkbox_group2

    def get_values(self) -> tuple[GameFlag | None, GameFlag | None]:
        raw_value1 = GameFlag(sum(int(value) for value in self.checkbox_group1.values))
        raw_value2 = GameFlag(sum(int(value) for value in self.checkbox_group2.values))
        return self._get_values(raw_value1, raw_value2)


class CommunityConfigEditPlatformFilterModal(
    _CommunityConfigEditModal[PlatformFlag | None]
):
    def setup_modal_components(self) -> None:
        value1, value2 = self.option.get_values(self.community)

        self.checkbox_group1 = discord.ui.CheckboxGroup(
            required=True,
            min_values=1,
            options=[
                discord.CheckboxGroupOption(
                    label="PC",
                    value=str(PlatformFlag.PC.value),
                    default=value1 is None or (value1 & PlatformFlag.PC) != 0,
                ),
                discord.CheckboxGroupOption(
                    label="Console",
                    value=str(PlatformFlag.CONSOLE.value),
                    default=value1 is None or (value1 & PlatformFlag.CONSOLE) != 0,
                ),
            ],
        )

        self.checkbox_group2 = discord.ui.CheckboxGroup(
            required=True,
            min_values=1,
            options=[
                discord.CheckboxGroupOption(
                    label="PC",
                    value=str(PlatformFlag.PC.value),
                    default=value2 is None or (value2 & PlatformFlag.PC) != 0,
                ),
                discord.CheckboxGroupOption(
                    label="Console",
                    value=str(PlatformFlag.CONSOLE.value),
                    default=value2 is None or (value2 & PlatformFlag.CONSOLE) != 0,
                ),
            ],
        )

    def get_components(self) -> tuple[discord.ui.Item, discord.ui.Item]:
        return self.checkbox_group1, self.checkbox_group2

    def get_values(self) -> tuple[PlatformFlag | None, PlatformFlag | None]:
        raw_value1 = PlatformFlag(
            sum(int(value) for value in self.checkbox_group1.values)
        )
        raw_value2 = PlatformFlag(
            sum(int(value) for value in self.checkbox_group2.values)
        )
        return self._get_values(raw_value1, raw_value2)


class CommunityConfigEditReasonFilterModal(
    _CommunityConfigEditModal[ReportReasonFlag | None]
):
    def setup_modal_components(self) -> None:
        value1, value2 = self.option.get_values(self.community)

        options = [
            discord.SelectOption(
                label=reason.value.pretty_name,
                emoji=reason.value.emoji,
                value=reason.value.pretty_name,
            )
            for reason in ReportReasonDetails
        ] + [
            discord.SelectOption(
                label="Custom",
                emoji="🎲",
                value="Custom",
            )
        ]

        select1_options = [option.copy() for option in options]
        select1_default_values = (
            value1.to_list(custom_msg="Custom")
            if value1 and value1 != ReportReasonFlag.all()
            else []
        )
        for option in select1_options:
            if option.value in select1_default_values:
                option.default = True

        select2_options = [option.copy() for option in options]
        select2_default_values = (
            value2.to_list(custom_msg="Custom")
            if value2 and value2 != ReportReasonFlag.all()
            else []
        )
        for option in select2_options:
            if option.value in select2_default_values:
                option.default = True

        self.select1 = discord.ui.Select(
            placeholder="All reasons allowed",
            required=False,
            min_values=0,
            max_values=len(options),
            options=select1_options,
        )

        self.select2 = discord.ui.Select(
            placeholder="All reasons allowed",
            required=False,
            min_values=0,
            max_values=len(options),
            options=select2_options,
        )

    def get_components(self) -> tuple[discord.ui.Item, discord.ui.Item]:
        return self.select1, self.select2

    def get_values(self) -> tuple[ReportReasonFlag | None, ReportReasonFlag | None]:
        raw_value1 = (
            ReportReasonFlag.from_list(self.select1.values)[0]
            if self.select1.values
            else ReportReasonFlag.all()
        )
        raw_value2 = (
            ReportReasonFlag.from_list(self.select2.values)[0]
            if self.select2.values
            else ReportReasonFlag.all()
        )
        return self._get_values(raw_value1, raw_value2)


def get_community_config_edit_modal(
    community: schemas.Community,
    option: ConfigOption,
) -> _CommunityConfigEditModal:
    match option.type:
        case ConfigOptionType.TEXT_CHANNEL:
            return CommunityConfigEditTextChannelModal(community, option)
        case ConfigOptionType.ROLE:
            return CommunityConfigEditRoleModal(community, option)
        case ConfigOptionType.GAME_FILTER:
            return CommunityConfigEditGameFilterModal(community, option)
        case ConfigOptionType.PLATFORM_FILTER:
            return CommunityConfigEditPlatformFilterModal(community, option)
        case ConfigOptionType.REASON_FILTER:
            return CommunityConfigEditReasonFilterModal(community, option)
        case _:
            assert_never(option.type)
            raise ValueError(f"Unknown option type: {option.type}")
