from datetime import datetime
import re
from typing import Sequence, TypedDict
import aiohttp
import logging

from bunker import schemas
from bunker.crud.bans import expire_bans_of_player, get_bans_by_integration
from bunker.db import session_factory
from bunker.enums import Emojis, IntegrationType
from bunker.exceptions import (
    IntegrationBanError, IntegrationCommandError, NotFoundError,
    AlreadyBannedError, IntegrationValidationError
)
from bunker.integrations.custom import CustomIntegration
from bunker.integrations.integration import IntegrationMetaData
from bunker.integrations.websocket import (
    BanPlayersRequestConfigPayload, BanPlayersRequestPayload, ClientRequestType,
    UnbanPlayersRequestPayload
)

RE_VERSION = re.compile(r"v(?P<major>\d+).(?P<minor>\d+).(?P<patch>\d+)")

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

class CRCONIntegration(CustomIntegration):
    meta = IntegrationMetaData(
        name="Community RCON",
        config_cls=schemas.CRCONIntegrationConfig,
        type=IntegrationType.COMMUNITY_RCON,
        emoji=Emojis.CRCON,
    )

    def __init__(self, config: schemas.CRCONIntegrationConfigParams) -> None:
        super().__init__(config)
        self.config: schemas.CRCONIntegrationConfigParams
    
    def get_ws_url(self):
        return self.config.api_url + "/ws/barricade"

    # --- Abstract method implementations

    async def get_instance_name(self) -> str:
        resp = await self._make_request(method="GET", endpoint="/public_info")
        return resp["result"]["short_name"]
    
    def get_instance_url(self) -> str:
        return self.config.api_url.removesuffix("/api")

    async def validate(self, community: schemas.Community):
        await super().validate(community)

        if not self.config.api_url.endswith("/api"):
            raise IntegrationValidationError("API URL does not end with \"/api\"")
        
        await self.validate_api_access()

    async def synchronize(self):
        remote_bans = await self.get_blacklist_bans()
        async with session_factory.begin() as db:
            async for db_ban in get_bans_by_integration(db, self.config.id):
                remote_ban = remote_bans.pop(db_ban.remote_id, None)
                if not remote_ban:
                    await db.delete(db_ban)

                elif not remote_ban["is_active"]:
                    # The player was unbanned, change responses of all reports where
                    # the player is banned
                    with session_factory.begin() as _db:
                        await expire_bans_of_player(_db, db_ban.player_id, db_ban.integration.community_id)
            
            for remote_ban in remote_bans.values():
                logging.warn("Ban exists on the remote but not locally, removing: %r", remote_ban)
                await self.remove_ban(remote_ban["id"])

    # --- CRCON API wrappers

    async def validate_api_access(self):
        try:
            resp = await self._make_request(method="GET", endpoint="/is_logged_in")
        except Exception as e:
            raise IntegrationValidationError("Failed to connect") from e
        is_auth = resp.get("authenticated")
        if is_auth is False:
            raise IntegrationValidationError("Invalid API key")
        elif is_auth is not True:
            raise IntegrationValidationError("Received unexpected API response")
        
        resp = await self._make_request(method="GET", endpoint="/get_version")
        version = resp["result"].strip()
        match = RE_VERSION.match(version)
        if not match:
            raise IntegrationValidationError('Unknown CRCON version "%s"' % version)
        version_numbers = [int(num) for num in match.groups()]
        if (
            version_numbers[0] < 10 or
            (version_numbers[0] == 10 and version_numbers[1] <= 0)
        ):
            raise IntegrationValidationError('Oudated CRCON version')

    async def get_blacklist_bans(self):
        records: dict[str, BlacklistRecord] = {}
        page = 1
        page_size = 100
        while True:
            resp = await self._make_request(
                "GET", "/get_blacklist_bans",
                data=dict(
                    blacklist_id=self.config.banlist_id,
                    exclude_expired=True,
                    page_size=page_size,
                    page=page,
                )    
            )
            result = resp["result"]

            for record in result["records"]:
                records[record["id"]] = record

            if page * page_size >= result["total"]:
                break

            page += 1
        return records
