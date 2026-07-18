import re
from datetime import UTC, datetime
from typing import TypedDict

from aiohttp import ClientResponseError

from barricade import schemas
from barricade.crud.bans import (
    expire_bans_of_player,
    get_bans_by_integration,
)
from barricade.crud.communities import get_community_by_id
from barricade.db import session_factory
from barricade.discord.communities import safe_send_to_community
from barricade.discord.utils import get_danger_embed
from barricade.enums import Emojis, Game, IntegrationType
from barricade.exceptions import (
    IntegrationValidationError,
)
from barricade.integrations.custom import CustomIntegration, is_websocket_enabled
from barricade.integrations.integration import IntegrationMetaData, is_enabled
from barricade.integrations.mixins import (
    IntegrationBanListMixin,
    IntegrationScopedMixin,
)
from barricade.integrations.scope import Scope
from barricade.utils import async_ttl_cache, game_switch

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


class CRCONIntegration(
    IntegrationScopedMixin,
    IntegrationBanListMixin,
    CustomIntegration,
):
    meta = IntegrationMetaData(
        name="Community RCON",
        config_cls=schemas.CRCONIntegrationConfig,
        type=IntegrationType.COMMUNITY_RCON,
        emoji=Emojis.CRCON,
    )

    def __init__(self, config: schemas.CRCONIntegrationConfigParams) -> None:
        super().__init__(config)
        self.config: schemas.CRCONIntegrationConfigParams  # type: ignore

    def get_api_url(self):
        return self.config.api_url + "/api"

    def get_ws_url(self):
        return self.config.api_url + "/ws/barricade"

    # --- Abstract method implementations

    @async_ttl_cache(size=9999, seconds=60 * 10)
    async def get_instance_name(self) -> str:
        resp = await self._make_request(method="GET", endpoint="/get_public_info")
        return resp["result"]["name"]["short_name"]

    def get_instance_url(self) -> str | None:
        return self.config.api_url

    async def validate(self, community: schemas.Community) -> set[str]:
        await self.validate_crcon_version()

        await super().validate(community)

        missing_optional_perms = await self.validate_scopes()
        await self.validate_ban_lists(community)

        return missing_optional_perms

    @is_enabled
    @is_websocket_enabled
    async def synchronize(self):
        if not self.config.id:
            raise RuntimeError("Integration has not yet been saved")

        async with session_factory.begin() as db:
            # Get community details
            db_community = await get_community_by_id(db, self.config.community_id)
            community = schemas.CommunityRef.model_validate(db_community)

            # Bans are grouped by game. Iterate over each game.
            for game in Game:
                # Fetch bans from remote list
                remote_bans = await self.get_blacklist_bans(game)

                # Iterate over bans from local database. Update to match remote bans.
                async for db_ban in get_bans_by_integration(
                    db, self.config.id, game=game
                ):
                    # Find matching remote ban
                    remote_ban = remote_bans.pop(db_ban.remote_id, None)

                    # Delete local ban if no remote ban exists
                    if not remote_ban:
                        await db.delete(db_ban)

                    # Expire local ban if remote ban is expired
                    elif not remote_ban["is_active"]:
                        # The player was unbanned, change responses of all reports where
                        # the player is banned
                        async with session_factory.begin() as _db:
                            # TODO: Remove the remote ban?
                            await expire_bans_of_player(
                                _db, db_ban.player_id, db_ban.integration.community_id
                            )

                # Iterate over remaining remote bans of which no local ban exists. Expire them.
                for remote_ban in remote_bans.values():
                    # Skip already expired bans
                    if not remote_ban["is_active"]:
                        continue

                    embed = get_danger_embed(
                        "Found unrecognized ban on CRCON blacklist!",
                        (
                            f"-# Your Barricade blacklist contained [an active ban]({self.config.api_url.removesuffix('api')}#/blacklists) that Barricade does not recognize."
                            " Please do not put any of your own bans on this blacklist."
                            "\n\n"
                            "-# The ban has been expired. If you wish to restore it, move it to a different blacklist first. If this is a Barricade ban, feel free to ignore this."
                        ),
                    )
                    self.logger.warning(
                        "Ban exists on the remote but not locally, expiring: %r",
                        remote_ban,
                    )
                    await self.expire_ban(remote_ban["id"])
                    safe_send_to_community(community, embed=embed, game=game)

    # --- Scoped integration mixin

    def get_required_scopes(self) -> set[Scope]:
        return REQUIRED_SCOPES

    def get_optional_scopes(self) -> set[Scope]:
        return set()

    async def get_scopes(self) -> set[Scope]:
        # Check if the user has all the required perms
        try:
            resp = await self._make_request(
                method="GET", endpoint="/get_own_user_permissions"
            )
        except Exception as e:
            if isinstance(e, ClientResponseError) and e.status == 401:
                raise IntegrationValidationError("Invalid API key") from None
            raise IntegrationValidationError("Failed to connect") from e

        result = resp["result"]
        is_superuser = result.get("is_superuser", False)

        if is_superuser:
            return self.get_required_scopes() | self.get_optional_scopes()

        return {Scope(p["permission"]) for p in result.get("permissions", [])}

    # --- Ban list integration mixin

    async def create_remote_ban_list(self, community: schemas.Community, game: Game):
        self.logger.info("%r: Creating new blacklist for game %s", self, game)
        resp = await self._make_request(
            method="POST",
            endpoint="/create_blacklist",
            data={
                "name": f"{game.name} Barricade - {community.name} (ID: {community.id})",
                "sync_method": "kick_only",
            },
        )
        return str(resp["result"]["id"])

    async def validate_remote_ban_list(self, banlist_id: str) -> None:
        # we use get_blacklists instead of get_blacklist because the latter will also fetch
        # and return all bans which makes it very inefficient for simply checking its existence.
        blacklist_ids = await self.get_remote_ban_list_ids()
        if banlist_id not in blacklist_ids:
            raise IntegrationValidationError(
                f"Ban list with ID {banlist_id} does not exist on the remote"
            )

    # --- CRCON API wrappers

    async def get_remote_ban_list_ids(self) -> set[str]:
        resp = await self._make_request(
            method="GET",
            endpoint="/get_blacklists",
        )
        blacklists = resp["result"]
        blacklist_ids = {str(blacklist["id"]) for blacklist in blacklists}
        return blacklist_ids

    async def validate_crcon_version(self):
        # Fetch CRCON version
        try:
            resp = await self._make_request(method="GET", endpoint="/get_version")
        except Exception as e:
            raise IntegrationValidationError("Failed to connect") from e

        # Check if version is sufficient
        version = resp["result"].strip()
        match = RE_VERSION.match(version)
        if not match:
            raise IntegrationValidationError(f'Unknown CRCON version "{version}"')
        version_numbers = [int(num) for num in match.groups()]
        if version_numbers[0] < 10:
            raise IntegrationValidationError(
                "Oudated CRCON version, v10 or above is required"
            )

    async def get_blacklist_bans(self, game: Game) -> dict[str, BlacklistRecord]:
        records: dict[str, BlacklistRecord] = {}
        page = 1
        page_size = 100
        banlist_id = game_switch(
            game, self.config.hll_banlist_id, self.config.hllv_banlist_id
        )
        while True:
            resp = await self._make_request(
                "GET",
                "/get_blacklist_records",
                data=dict(
                    blacklist_id=banlist_id,
                    exclude_expired=1,
                    page_size=page_size,
                    page=page,
                ),
            )
            result = resp["result"]

            for record in result["records"]:
                records[str(record["id"])] = record

            if page * page_size >= result["total"]:
                break

            page += 1
        return records

    async def expire_ban(self, record_id: int):
        self.logger.info("%r: Expiring record %s", self, record_id)
        await self._make_request(
            "POST",
            "/edit_blacklist_record",
            data=dict(
                record_id=record_id,
                expires_at=datetime.now(tz=UTC).isoformat(),
            ),
        )

    async def get_player_eos_ids(self, player_id: str) -> tuple[str | None, str | None]:
        resp = await self._make_request(
            "GET", "/get_player_profile", data=dict(player_id=player_id)
        )

        result = resp["result"]
        if result is None:
            self.logger.warning(
                "%r: No profile found for player_id %s", self, player_id
            )
            return None, None

        # TODO: Fetch HLLV EOS ID
        soldier_data = result.get("soldier", {})
        return soldier_data.get("eos_id"), None
