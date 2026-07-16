import re
from datetime import datetime
from typing import TypedDict

from barricade import schemas
from barricade.enums import Emojis, IntegrationType
from barricade.integrations.custom import CustomIntegration, is_websocket_enabled
from barricade.integrations.integration import IntegrationMetaData, is_enabled
from barricade.integrations.scope import Scope
from barricade.utils import async_ttl_cache

RE_VERSION = re.compile(r"v(?P<major>\d+).(?P<minor>\d+).(?P<patch>\d+)")

REQUIRED_PERMISSIONS = {
    "can_view_blacklists",
    "can_create_blacklists",
    "can_add_blacklist_records",
    "can_change_blacklist_records",
    "can_delete_blacklist_records",
    "can_view_player_profile",
}

REQUIRED_SCOPES = {
    Scope("can_view_blacklists"),
    Scope("can_create_blacklists"),
    Scope("can_add_blacklist_records"),
    Scope("can_change_blacklist_records"),
    Scope("can_delete_blacklist_records"),
    Scope("can_view_player_profile"),
}


class Blacklist(TypedDict):
    id: int
    name: str
    sync: str
    servers: list[int] | None


class PlayerName(TypedDict):
    id: int
    name: str
    player_id: str
    created: datetime
    last_seen: datetime


class Player(TypedDict):
    id: int
    player_id: str
    created: datetime
    names: list[PlayerName]
    steaminfo: dict | None


class BlacklistRecord(TypedDict):
    id: int
    player_id: str
    reason: str
    admin_name: str
    created_at: datetime
    expires_at: datetime | None
    is_active: bool
    blacklist: Blacklist
    player: Player
    formatted_reason: str


class BifrostIntegration(
    CustomIntegration,
):
    meta = IntegrationMetaData(
        name="Bifrost",
        config_cls=schemas.BifrostIntegrationConfig,
        type=IntegrationType.BIFROST,
        emoji=Emojis.BIFROST,
    )

    def __init__(self, config: schemas.BifrostIntegrationConfigParams) -> None:
        super().__init__(config)
        self.config: schemas.BifrostIntegrationConfigParams  # type: ignore

    def get_api_url(self):
        return self.config.api_url + "/api"

    def get_ws_url(self):
        return self.config.api_url + "/ws/barricade"

    # --- Abstract method implementations

    @async_ttl_cache(size=9999, seconds=60 * 10)
    async def get_instance_name(self) -> str:
        # TODO: Get Bifrost instance name
        return "Bifrost"

    def get_instance_url(self) -> str | None:
        return "https://dashboard.bifrostgaming.com/"

    # TODO: Validate that API token is valid
    # async def validate(self, community: schemas.Community) -> set[str]:
    #     await super().validate(community)
    #     return set()

    @is_enabled
    @is_websocket_enabled
    async def synchronize(self):
        pass

    # --- Bifrost API wrappers
