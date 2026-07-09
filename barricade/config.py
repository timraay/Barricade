from enum import StrEnum
from typing import Generic, TypeVar

from pydantic import BaseModel, field_validator

from barricade import schemas

T = TypeVar("T")


class ConfigOptionCategory(StrEnum):
    CHANNELS = "Channels"
    ROLES = "Roles"
    FILTERS = "Filters"
    INTEGRATIONS = "Integrations"


class ConfigOptionType(StrEnum):
    TEXT_CHANNEL = "Text Channel"
    ROLE = "Role"
    PLATFORM_FILTER = "Platform Filter"
    REASON_FILTER = "Reason Filter"


class ConfigOption(BaseModel, Generic[T]):
    id: str
    name: str
    description: str
    type: ConfigOptionType
    category: ConfigOptionCategory
    property_ids: tuple[str, str | None]
    can_inherit_from: str | None = None
    is_bound_to_guild: bool = True
    is_nullable: bool = False

    def is_game_dependent(self) -> bool:
        return self.property_ids[1] is not None

    @field_validator("property_ids", mode="after")
    @classmethod
    def validate_property_ids(
        cls, property_ids: tuple[str, str | None]
    ) -> tuple[str, str | None]:
        property_id1, property_id2 = property_ids
        if property_id1 not in schemas.CommunityRef.model_fields:
            raise ValueError(f"Property {property_id1} does not exist on CommunityRef")
        if property_id2 and property_id2 not in schemas.CommunityRef.model_fields:
            raise ValueError(f"Property {property_id2} does not exist on CommunityRef")
        return property_ids

    def get_values(self, community: schemas.CommunityRef) -> tuple[T | None, T | None]:
        property_id1, property_id2 = self.property_ids
        value1 = getattr(community, property_id1)
        value2 = getattr(community, property_id2) if property_id2 else None
        return value1, value2

    def set_values(
        self, community: schemas.CommunityRef, value1: T | None, value2: T | None = None
    ):
        property_id1, property_id2 = self.property_ids
        setattr(community, property_id1, value1)
        if property_id2:
            setattr(community, property_id2, value2)


CONFIG_OPTIONS: dict[str, ConfigOption] = {
    option.id: option
    for option in [
        # Channels
        ConfigOption(
            id="reports_channel_id",
            name="Reports Channel",
            description="The text channel where you receive new reports.",
            type=ConfigOptionType.TEXT_CHANNEL,
            category=ConfigOptionCategory.CHANNELS,
            property_ids=(
                "hll_reports_channel_id",
                "hllv_reports_channel_id",
            ),
            is_nullable=True,
        ),
        ConfigOption(
            id="alerts_channel_id",
            name="Alerts Channel",
            description="The text channel where you receive alerts.",
            type=ConfigOptionType.TEXT_CHANNEL,
            category=ConfigOptionCategory.CHANNELS,
            property_ids=(
                "hll_alerts_channel_id",
                "hllv_alerts_channel_id",
            ),
            can_inherit_from="Reports Channel",
            is_nullable=True,
        ),
        ConfigOption(
            id="confirmations_channel_id",
            name="Confirmations Channel",
            description="The text channel where you receive confirmations of reports you submit.",
            type=ConfigOptionType.TEXT_CHANNEL,
            category=ConfigOptionCategory.CHANNELS,
            property_ids=(
                "hll_confirmations_channel_id",
                "hllv_confirmations_channel_id",
            ),
            can_inherit_from="Reports Channel",
            is_nullable=True,
        ),
        # Roles
        ConfigOption(
            id="admin_role_id",
            name="Admin Role",
            description="The role that can review reports.",
            type=ConfigOptionType.ROLE,
            category=ConfigOptionCategory.ROLES,
            property_ids=(
                "hll_admin_role_id",
                "hllv_admin_role_id",
            ),
        ),
        ConfigOption(
            id="alerts_role_id",
            name="Alerts Role",
            description="The role that gets notified by alerts.",
            type=ConfigOptionType.ROLE,
            category=ConfigOptionCategory.ROLES,
            property_ids=(
                "hll_alerts_role_id",
                "hllv_alerts_role_id",
            ),
            can_inherit_from="Admin Role",
            is_nullable=True,
        ),
        # Filters
        ConfigOption(
            id="platform_filter",
            name="Report Platform Filter",
            description="Which platforms (i.e. PC & Console) to receive reports from.",
            type=ConfigOptionType.PLATFORM_FILTER,
            category=ConfigOptionCategory.FILTERS,
            property_ids=(
                "hll_platform_filter",
                "hllv_platform_filter",
            ),
        ),
        ConfigOption(
            id="reason_filter",
            name="Report Reason Filter",
            description="Which categories of reports to receive.",
            type=ConfigOptionType.REASON_FILTER,
            category=ConfigOptionCategory.FILTERS,
            property_ids=(
                "hll_reason_filter",
                "hllv_reason_filter",
            ),
        ),
    ]
}
