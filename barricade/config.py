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
    REASON_FILTER = "Reason Filter"


class ConfigOption(BaseModel, Generic[T]):
    name: str
    description: str
    type: ConfigOptionType
    category: ConfigOptionCategory
    property_ids: tuple[str, str | None]
    can_inherit_from: str | None = None

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

    def get_values(self, community: schemas.CommunityRef) -> tuple[T, T | None]:
        property_id1, property_id2 = self.property_ids
        value1 = getattr(community, property_id1)
        value2 = getattr(community, property_id2) if property_id2 else None
        return value1, value2

    def set_values(
        self, community: schemas.CommunityRef, value1: T, value2: T | None = None
    ):
        property_id1, property_id2 = self.property_ids
        setattr(community, property_id1, value1)
        if property_id2:
            setattr(community, property_id2, value2)


CONFIG_OPTIONS: list[ConfigOption] = [
    # Channels
    ConfigOption(
        name="Reports Feed",
        description="The text channel where you receive new reports.",
        type=ConfigOptionType.TEXT_CHANNEL,
        category=ConfigOptionCategory.CHANNELS,
        property_ids=("forward_channel_id", None),
    ),
    ConfigOption(
        name="Alerts Feed",
        description="The text channel where you receive alerts.",
        type=ConfigOptionType.TEXT_CHANNEL,
        category=ConfigOptionCategory.CHANNELS,
        property_ids=("alerts_channel_id", None),
        can_inherit_from="Reports Feed",
    ),
    ConfigOption(
        name="Confirmations Feed",
        description="The text channel where you receive confirmations of reports you submit.",
        type=ConfigOptionType.TEXT_CHANNEL,
        category=ConfigOptionCategory.CHANNELS,
        property_ids=("confirmations_channel_id", None),
        can_inherit_from="Reports Feed",
    ),
    # Roles
    ConfigOption(
        name="Admin Role",
        description="The role that can review reports.",
        type=ConfigOptionType.ROLE,
        category=ConfigOptionCategory.ROLES,
        property_ids=("admin_role_id", None),
    ),
    ConfigOption(
        name="Alerts Role",
        description="The role that gets notified by alerts.",
        type=ConfigOptionType.ROLE,
        category=ConfigOptionCategory.ROLES,
        property_ids=("alerts_role_id", None),
        can_inherit_from="Admin Role",
    ),
    # Filters
    ConfigOption(
        name="Report Reason Filter",
        description="Which categories of reports to receive.",
        type=ConfigOptionType.REASON_FILTER,
        category=ConfigOptionCategory.FILTERS,
        property_ids=("reasons_filter", None),
    ),
]
