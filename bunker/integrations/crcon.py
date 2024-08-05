from datetime import datetime
import re
from typing import Sequence, TypedDict
import aiohttp
import logging

from bunker import schemas
from bunker.crud.bans import expire_bans_of_player, get_bans_by_integration
from bunker.db import session_factory
from bunker.enums import IntegrationType
from bunker.exceptions import (
    IntegrationBanError, IntegrationCommandError, NotFoundError,
    AlreadyBannedError, IntegrationValidationError
)
from bunker.integrations.integration import Integration, IntegrationMetaData
from bunker.integrations.websocket import (
    BanPlayersRequestConfigPayload, BanPlayersRequestPayload, ClientRequestType,
    UnbanPlayersRequestPayload, Websocket
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

class CRCONIntegration(Integration):
    meta = IntegrationMetaData(
        name="Community RCON",
        config_cls=schemas.CRCONIntegrationConfig,
        type=IntegrationType.COMMUNITY_RCON,
        emoji="🤩",
    )

    def __init__(self, config: schemas.CRCONIntegrationConfigParams) -> None:
        super().__init__(config)
        self.config: schemas.CRCONIntegrationConfigParams
        self.ws = Websocket(address=config.api_url, token=config.api_key)

    # --- Extended parent methods

    def start_connection(self):
        self.ws.start()
    
    def stop_connection(self):
        self.ws.stop()
    
    def update_connection(self):
        self.ws.address = self.config.api_url
        self.ws.token = self.config.api_key
        self.ws.update_connection()

    # --- Abstract method implementations

    async def get_instance_name(self) -> str:
        resp = await self._make_request(method="GET", endpoint="/public_info")
        return resp["result"]["short_name"]
    
    def get_instance_url(self) -> str:
        return self.config.api_url.removesuffix("/api")

    async def validate(self, community: schemas.Community):
        if community.id != self.config.community_id:
            raise IntegrationValidationError("Communities do not match")

        if not self.config.api_url.endswith("/api"):
            raise IntegrationValidationError("API URL does not end with \"/api\"")
        
        await self.validate_api_access()

    async def ban_player(self, response: schemas.Response):
        async with session_factory.begin() as db:
            player_id = response.player_report.player_id
            db_ban = await self.get_ban(db, player_id)
            if db_ban is not None:
                raise AlreadyBannedError(player_id, "Player is already banned")

            try:
                await self.add_ban(
                    player_id=player_id,
                    reason=self.get_ban_reason(response.community)
                )
            except Exception as e:
                raise IntegrationBanError(player_id, "Failed to ban player") from e

            await self.set_ban_id(db, player_id, player_id)

    async def unban_player(self, response: schemas.Response):
        async with session_factory.begin() as db:
            player_id = response.player_report.player_id
            db_ban = await self.get_ban(db, player_id)
            if db_ban is None:
                raise NotFoundError("Ban does not exist")

            await db.delete(db_ban)
            await db.flush()

            try:
                await self.remove_ban(db_ban.remote_id)
            except Exception as e:
                raise IntegrationBanError(player_id, "Failed to unban player") from e
    
    async def bulk_ban_players(self, responses: Sequence[schemas.Response]):
        ban_ids: list[tuple[str, str]] = []
        try:
            async for ban in self.add_multiple_bans(
                player_ids={
                    response.player_report.player_id: self.get_ban_reason(response.community)
                    for response in responses
                }
            ):
                ban_ids.append(ban)

        finally:
            if ban_ids:
                async with session_factory.begin() as db:
                    await self.set_multiple_ban_ids(db, ban_ids)

    async def bulk_unban_players(self, responses: Sequence[schemas.Response]):
        async with session_factory() as db:
            player_ids: dict[str, str] = {}
            for response in responses:
                player_id = response.player_report.player_id
                ban = await self.get_ban(db, player_id)
                player_ids[ban.remote_id] = player_id

        successful_player_ids: list[str] = []
        try:
            async for ban_id in self.remove_multiple_bans(ban_ids=player_ids.keys()):
                successful_player_ids.append(player_ids[ban_id])
        finally:
            if successful_player_ids:
                async with session_factory.begin() as db:
                    await self.discard_multiple_ban_ids(db, successful_player_ids)

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

    async def _make_request(self, method: str, endpoint: str, data: dict = None) -> dict:
        """Make an API request.

        Parameters
        ----------
        method : str
            One of GET, POST, PATCH, DELETE
        endpoint : str
            The resource to query, gets prepended with the API root URL.
            For example, `/login` queries `http://<api>:<port>/api/login`.
        data : dict, optional
            Additional data to include in the request, by default None

        Returns
        -------
        dict
            The response from the server

        Raises
        ------
        Exception
            Doom and gloom
        """
        url = self.config.api_url + endpoint
        headers = {"Authorization": f"Bearer {self.config.api_key}"}
        async with aiohttp.ClientSession(headers=headers) as session:
            if method in {"POST", "PATCH"}:
                kwargs = {"json": data}
            else:
                kwargs = {"params": data}

            async with session.request(method=method, url=url, **kwargs) as r:
                r.raise_for_status()
                content_type = r.headers.get('content-type', '')

                if 'json' in content_type:
                    response = await r.json()
                elif "text/html" in content_type:
                    response = (await r.content.read()).decode()
                else:
                    raise Exception(f"Unsupported content type: {content_type}")

        return response
    
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

    async def add_multiple_bans(self, player_ids: dict[str, str | None], *, partial_retry: bool = True):
        try:
            response = await self.ws.execute(ClientRequestType.BAN_PLAYERS, BanPlayersRequestPayload(
                player_ids=player_ids,
                config=BanPlayersRequestConfigPayload(
                    banlist_id=self.config.banlist_id,
                    reason="Banned via shared HLL Bunker report.",
                )
            ))
        except IntegrationCommandError as e:
            if e.response.get("error") != "Could not ban all players":
                raise

            successful_ids = e.response["player_ids"]
            for player_id, ban_id in successful_ids.items():
                yield (player_id, ban_id)
            
            if not partial_retry:
                raise

            # Retry for failed player IDs
            missing_player_ids = {k: v for k, v in player_ids.items() if k not in successful_ids}
            async for (player_id, ban_id) in self.add_multiple_bans(missing_player_ids, partial_retry=False):
                yield player_id, ban_id
        else:
            for player_id, ban_id in response["player_ids"]:
                yield player_id, ban_id

    async def remove_multiple_bans(self, ban_ids: Sequence[str], *, partial_retry: bool = True):
        try:
            response = await self.ws.execute(ClientRequestType.UNBAN_PLAYERS, UnbanPlayersRequestPayload(
                ban_ids=ban_ids,
                config=BanPlayersRequestConfigPayload(
                    banlist_id=self.config.banlist_id,
                )
            ))
        except IntegrationCommandError as e:
            if e.response.get("error") != "Could not unban all players":
                raise

            successful_ids = e.response["ban_ids"]
            for ban_id in successful_ids:
                yield ban_id
            
            if not partial_retry:
                raise

            # Retry for failed ban IDs
            missing_ban_ids = list(set(ban_ids) - set(successful_ids))
            async for ban_id in self.remove_multiple_bans(missing_ban_ids, partial_retry=False):
                yield ban_id
        else:
            for ban_id in response["ban_ids"]:
                yield ban_id

    async def add_ban(self, player_id: str, reason: str | None = None):
        return await anext(self.add_multiple_bans({player_id: reason}))

    async def remove_ban(self, ban_id: str):
        return await anext(self.remove_multiple_bans([ban_id]))
