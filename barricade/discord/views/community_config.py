import re
from collections.abc import Callable
from functools import partial
from typing import TypeVar, assert_never

import discord

from barricade import schemas
from barricade.config import (
    CONFIG_OPTIONS,
    ConfigOption,
    ConfigOptionCategory,
    ConfigOptionType,
)
from barricade.crud.communities import get_community_by_id
from barricade.db import session_factory
from barricade.discord.communities import get_text_channel
from barricade.discord.utils import CustomException, LayoutView, handle_error_wrap
from barricade.enums import ReportReasonFlag

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
            db_community = await get_community_by_id(db, self.community_id)
            if not db_community:
                raise CustomException("Community not found")
            community = schemas.Community.model_validate(db_community)

        view = get_community_config_view(community, self.category)
        await interaction.response.edit_message(view=view)


# class CommunityConfigEditButton(
#     discord.ui.DynamicItem[discord.ui.Button],
#     template=r"community:(?P<community_id>):config:edit:(?P<field>)",
# ):
#     # TODO
#     pass


class CommunityConfigView(LayoutView):
    def __init__(
        self,
        community: schemas.Community,
        category: ConfigOptionCategory,
    ):
        super().__init__(timeout=None)
        self.community = community
        self.category = category

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
        for option in CONFIG_OPTIONS:
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

            value_string = option_values_to_string(self.community, option)
            value_display = discord.ui.TextDisplay(f">>> {value_string}")
            container.add_item(
                discord.ui.Section(
                    value_display,
                    accessory=discord.ui.Button(
                        style=discord.ButtonStyle.blurple,
                        label="Edit",
                    ),
                )
            )

        return container


def get_community_config_view(
    community: schemas.Community,
    category: ConfigOptionCategory = ConfigOptionCategory.CHANNELS,
) -> LayoutView:
    return CommunityConfigView(community, category)


def combine_option_value_strings(
    value_to_string_func: Callable[[T], str],
    value1: T,
    value2: T | None,
    can_inherit_from: str | None = None,
    multiline: bool = False,
) -> str:
    if value1 is None and value2 is None and can_inherit_from:
        # Inherit from parent
        return f"Same as **{can_inherit_from}**"

    if value2 is None:
        # value2 inherits from value1
        return value_to_string_func(value1)

    display1 = value_to_string_func(value1)
    display2 = value_to_string_func(value2)

    if multiline:
        return f"{display1}\n-# **HLL (WWII)\n{display2}\n-# **HLL: Vietnam**"
    return f"{display1} {display2}\n-# **HLL (WWII) | HLL: Vietnam"


def _text_channel_value_to_string(
    community: schemas.Community, value: int | None
) -> str:
    channel = get_text_channel(community.forward_guild_id, value)
    if channel:
        return channel.mention
    elif value:
        return "Unknown"
    return "None"


def text_channel_values_to_string(
    community: schemas.Community,
    value1: int | None,
    value2: int | None,
    can_inherit_from: str | None = None,
) -> str:
    return combine_option_value_strings(
        partial(_text_channel_value_to_string, community),
        value1,
        value2,
        can_inherit_from=can_inherit_from,
    )


def _role_value_to_string(value: int | None) -> str:
    return f"<@&{value}>" if value else "None"


def role_values_to_string(
    community: schemas.Community,
    value1: int | None,
    value2: int | None,
    can_inherit_from: str | None = None,
) -> str:
    return combine_option_value_strings(
        _role_value_to_string, value1, value2, can_inherit_from=can_inherit_from
    )


def _reason_filter_value_to_string(value: ReportReasonFlag | None) -> str:
    if not value:
        return "All"
    elif value == 0:
        return "None"
    return "\n- ".join(value.to_list(custom_msg="Custom", with_emoji=True))


def reason_filter_values_to_string(
    community: schemas.Community,
    value1: ReportReasonFlag | None,
    value2: ReportReasonFlag | None,
    can_inherit_from: str | None = None,
) -> str:
    return combine_option_value_strings(
        _reason_filter_value_to_string,
        value1,
        value2,
        multiline=True,
        can_inherit_from=can_inherit_from,
    )


def option_values_to_string(
    community: schemas.Community,
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
        case ConfigOptionType.REASON_FILTER:
            assert isinstance(value1, ReportReasonFlag | None)
            assert isinstance(value2, ReportReasonFlag | None)
            return reason_filter_values_to_string(
                community, value1, value2, can_inherit_from=option.can_inherit_from
            )
        case _:
            assert_never(option.type)
            raise ValueError(f"Unknown option type: {option.type}")
