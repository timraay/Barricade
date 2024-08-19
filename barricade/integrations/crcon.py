from datetime import datetime, timezone
import re
from typing import TypedDict
import logging

from aiohttp import ClientResponseError

from barricade import schemas
from barricade.crud.bans import expire_bans_of_player, get_bans_by_integration
from barricade.crud.communities import get_community_by_id
from barricade.db import session_factory
from barricade.discord.communities import safe_send_to_community
from barricade.discord.utils import get_danger_embed
from barricade.enums import Emojis, IntegrationType
from barricade.exceptions import IntegrationValidationError
from barricade.integrations.custom import CustomIntegration
from barricade.integrations.integration import IntegrationMetaData

RE_VERSION = re.compile(r"v(?P<major>\d+).(?P<minor>\d+).(?P<patch>\d+)")

REQUIRED_PERMISSIONS = {
    "api.can_view_blacklists",
    "api.can_create_blacklists",
    "api.can_add_blacklist_records",
    "api.can_change_blacklist_records",
    "api.can_delete_blacklist_records",
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

class CRCONIntegration(CustomIntegration):
    meta = IntegrationMetaData(
        name="Community RCON",
        config_cls=schemas.CRCONIntegrationConfig,
        type=IntegrationType.COMMUNITY_RCON,
        emoji=Emojis.CRCON,
    )

    def __init__(self, config: schemas.CRCONIntegrationConfigParams) -> None:
        super().__init__(config)
        self.config: schemas.CRCONIntegrationConfigParams # type: ignore
    
    def get_ws_url(self):
        return self.config.api_url.removesuffix("/api") + "/ws/barricade"

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
        
        if not self.config.banlist_id:
            try:
                await self.create_blacklist(community)
            except Exception as e:
                raise IntegrationValidationError("Failed to create blacklist") from e
        else:
            await self.validate_blacklist()

    async def synchronize(self):
        if not self.config.id:
            raise RuntimeError("Integration has not yet been saved")
        
        remote_bans = await self.get_blacklist_bans()
        async with session_factory.begin() as db:
            db_community = await get_community_by_id(db, self.config.community_id)
            community = schemas.CommunityRef.model_validate(db_community)
            async for db_ban in get_bans_by_integration(db, self.config.id):
                remote_ban = remote_bans.pop(db_ban.remote_id, None)
                if not remote_ban:
                    await db.delete(db_ban)

                elif not remote_ban["is_active"]:
                    # The player was unbanned, change responses of all reports where
                    # the player is banned
                    async with session_factory.begin() as _db:
                        await expire_bans_of_player(_db, db_ban.player_id, db_ban.integration.community_id)
            
            for remote_ban in remote_bans.values():
                if not remote_ban["is_active"]:
                    continue

                embed = get_danger_embed(
                    "Found unrecognized ban on CRCON blacklist!",
                    (
                        f"-# Your Barricade blacklist contained [an active ban]({self.config.api_url.removesuffix('api')}#/blacklists) that Barricade does not recognize."
                        " Please do not put any of your own bans on this blacklist."
                        "\n\n"
                        "-# The ban has been expired. If you wish to restore it, move it to a different blacklist first. If this is a Barricade ban, feel free to ignore this."
                    )
                )
                logging.warn("Ban exists on the remote but not locally, expiring: %r", remote_ban)
                await self.expire_ban(remote_ban["id"])
                safe_send_to_community(community, embed=embed)

    # --- CRCON API wrappers

    async def validate_api_access(self):
        try:
            resp = await self._make_request(method="GET", endpoint="/get_own_user_permissions")
        except Exception as e:
            if isinstance(e, ClientResponseError) and e.status == 401:
                raise IntegrationValidationError("Invalid API key")
            raise IntegrationValidationError("Failed to connect") from e
        
        result = resp["result"]
        is_superuser = result.get("is_superuser", False)
        permissions = set(result.get("permissions", []))

        if not is_superuser and not permissions.issuperset(REQUIRED_PERMISSIONS):
            raise IntegrationValidationError("Missing permissions")
        
        resp = await self._make_request(method="GET", endpoint="/get_version")
        version = resp["result"].strip()
        match = RE_VERSION.match(version)
        if not match:
            raise IntegrationValidationError('Unknown CRCON version "%s"' % version)
        version_numbers = [int(num) for num in match.groups()]
        # if (
        #     version_numbers[0] < 10 or
        #     (version_numbers[0] == 10 and version_numbers[1] <= 0)
        # ):
        #     raise IntegrationValidationError('Oudated CRCON version')
    
    async def create_blacklist(self, community: schemas.Community):
        resp = await self._make_request(
            method="POST",
            endpoint="/create_blacklist",
            data={
                "name": f"HLL Barricade - {community.name} (ID: {community.id})",
                "sync_method": "kick_only",
            }
        )
        self.config.banlist_id = str(resp["result"]["id"])
    
    async def validate_blacklist(self):
        # we use get_blacklists instead of get_blacklist because the latter will also fetch
        # and return all bans which makes it very inefficient for simply checking its existence.
        resp = await self._make_request(
            method="GET",
            endpoint="/get_blacklists",
        )
        blacklists = resp["result"]
        for blacklist in blacklists:
            if str(blacklist["id"]) == self.config.banlist_id:
                return
        
        raise IntegrationValidationError("Failed to retrieve blacklist")

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
    
    async def expire_ban(self, record_id: int):
        await self._make_request(
            "PUT", "/edit_blacklist_record",
            data=dict(
                record_id=record_id,
                expires_at=datetime.now(tz=timezone.utc),
            )
        )