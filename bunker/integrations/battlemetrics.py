from datetime import datetime, timezone
import logging
from typing import Sequence, NamedTuple
import aiohttp
from uuid import UUID

from bunker import schemas
from bunker.crud.bans import get_bans_by_integration
from bunker.crud.responses import set_report_response
from bunker.db import session_factory
from bunker.enums import IntegrationType, PlayerIDType
from bunker.exceptions import IntegrationBanError, IntegrationBulkBanError, NotFoundError, IntegrationValidationError
from bunker.integrations.integration import Integration, IntegrationMetaData
from bunker.schemas import Response
from bunker.utils import get_player_id_type

REQUIRED_SCOPES = [
    "ban:create",
    "ban:edit",
    "ban:delete",
    "ban-list:create",
    "ban-list:read"
]

class BattlemetricsBan(NamedTuple):
    ban_id: int
    player_id: int
    expired: bool

class BattlemetricsIntegration(Integration):
    BASE_URL = "https://api.battlemetrics.com"

    meta = IntegrationMetaData(
        name="Battlemetrics",
        config_cls=schemas.BattlemetricsIntegrationConfig,
        type=IntegrationType.BATTLEMETRICS,
        ask_remove_bans=False,
        emoji="🤕",
    )

    def __init__(self, config: schemas.BattlemetricsIntegrationConfigParams) -> None:
        super().__init__(config)
        self.config: schemas.BattlemetricsIntegrationConfigParams

    # --- Abstract method implementations

    async def get_instance_name(self) -> str:
        url = f"{self.BASE_URL}/organizations/{self.config.organization_id}"
        resp = await self._make_request(method="GET", url=url)
        return resp["data"]["attributes"]["name"]
    
    def get_instance_url(self) -> str:
        return f"https://battlemetrics.com/rcon/orgs/{self.config.organization_id}/edit"

    async def validate(self, community: schemas.Community):
        if community.id != self.config.community_id:
            raise IntegrationValidationError("Communities do not match")

        await self.validate_scopes()

        if not self.config.banlist_id:
            try:
                await self.create_ban_list(community)
            except Exception as e:
                raise IntegrationValidationError("Failed to create ban list") from e
        else:
            await self.validate_ban_list()

    async def ban_player(self, params: schemas.IntegrationBanPlayerParams):
        async with session_factory.begin() as db:
            db_ban = await self.get_ban(db, params.player_id)
            if db_ban is not None:
                raise IntegrationBanError(params.player_id, "Player is already banned")

            reason = self.get_ban_reason(params.community)
            ban_id = await self.add_ban(
                identifier=params.player_id,
                reason=reason,
                note=f"Originally reported for {', '.join(params.reasons)}"
            )
            await self.set_ban_id(db, params.player_id, ban_id)

    async def unban_player(self, player_id: str):
        async with session_factory.begin() as db:
            db_ban = await self.get_ban(db, player_id)
            if db_ban is None:
                raise NotFoundError("Ban does not exist")

            try:
                await self.remove_ban(db_ban.remote_id)
            except aiohttp.ClientResponseError as e:
                if e.status == 404:
                    logging.error("Battlemetrics Ban with ID %s for player %s not found", db_ban.remote_id, player_id)
                else:
                    raise IntegrationBanError(player_id, "Failed to unban player")
            except:
                raise IntegrationBanError(player_id, "Failed to unban player")

            await db.delete(db_ban)

    async def bulk_ban_players(self, params: Sequence[schemas.IntegrationBanPlayerParams]):
        ban_ids = []
        failed = []
        for param in params:
            db_ban = await self.get_ban(db, param.player_id)
            if db_ban is not None:
                continue

            reason = self.get_ban_reason(param.community)
            try:
                ban_id = await self.add_ban(
                    identifier=param.player_id,
                    reason=reason,
                    note=f"Originally reported for {', '.join(param.reasons)}"
                )
            except IntegrationBanError:
                failed.append(param.player_id)
            else:
                ban_ids.append((param.player_id, ban_id))
        
        async with session_factory.begin() as db:
            await self.set_multiple_ban_ids(db, *ban_ids)
        
        if failed:
            raise IntegrationBulkBanError(failed, "Failed to ban players")

    async def bulk_unban_players(self, responses: Sequence[Response]):
        failed = []
        async with session_factory.begin() as db:
            for response in responses:
                db_ban = await self.get_ban(db, response)
                if not db_ban:
                    continue

                try:
                    await self.remove_ban(db_ban.remote_id)
                except IntegrationBanError:
                    failed.append(response.player_report.player_id)
                else:
                    await db.delete(db_ban)
        
        if failed:
            raise IntegrationBulkBanError(failed, "Failed to unban players")
    
    async def synchronize(self):
        remote_bans = await self.get_ban_list_bans()
        async with session_factory.begin() as db:
            async for db_ban in get_bans_by_integration(db, self.config.id):
                remote_ban = remote_bans.pop(db_ban.remote_id, None)
                if not remote_ban:
                    db.delete(db_ban)

                elif remote_ban.expired:
                    # TODO: Update report response
                    pass
            
            for remote_ban in remote_bans.values():
                logging.warn("Ban exists on the remote but not locally, removing: %r", remote_ban)
                await self.remove_ban(remote_ban.ban_id)

    # --- Battlemetrics API wrappers

    async def _make_request(self, method: str, url: str, data: dict = None) -> dict | None:
        """Make an API request.

        Parameters
        ----------
        method : str
            One of GET, POST, PATCH, DELETE
        url : str
            The resource to query
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
                elif not content_type:
                    response = None
                else:
                    raise Exception(f"Unsupported content type: {content_type}")

        return response 


    async def add_ban(self, identifier: str, reason: str, note: str) -> str:
        identifier_type = get_player_id_type(identifier)
        
        data = {
            "data": {
                "type": "ban",
                "attributes": {
                    "autoAddEnabled": True,
                    "expires": None,
                    "identifiers": [
                        {
                            "type": identifier_type.value,
                            "identifier": identifier,
                            "manual": True
                        }
                    ],
                    "nativeEnabled": None,
                    "reason": reason,
                    "note": note
                },
                "relationships": {
                    "organization": {
                        "data": {
                            "type": "organization",
                            "id": self.config.organization_id
                        }
                    },
                    "banList": {
                        "data": {
                            "type": "banList",
                            "id": str(self.config.banlist_id)
                        }
                    }
                }
            }
        }

        url = f"{self.BASE_URL}/bans"
        resp = await self._make_request(method="POST", url=url, data=data)

        return resp["data"]["id"]

    async def remove_ban(self, ban_id: str):
        url = f"{self.BASE_URL}/bans/{ban_id}"
        await self._make_request(method="DELETE", url=url)

    async def get_ban_list_bans(self) -> dict[str, BattlemetricsBan]:
        data = {
            "filter[banList]": str(self.config.banlist_id),
            "page[size]": 100,
            "filter[expired]": "false"
        }

        url = f"{self.BASE_URL}/bans"
        resp = await self._make_request(method="GET", url=url, data=data)
        responses = {}

        while resp["links"].get("next"):
            resp = await self._make_request(method="GET", url=resp["links"]["next"])
            for ban_data in resp["data"]:
                ban_attrs = ban_data["attributes"]

                ban_id = ban_data["id"]
                player_id = None

                # Find identifier of valid type
                identifiers = ban_attrs["identifiers"]
                for identifier_data in identifiers:
                    try:
                        PlayerIDType(identifier_data["type"])
                    except KeyError:
                        continue
                    player_id = identifier_data["identifier"]
                    break

                # If no valid identifier is found, skip this
                if not player_id:
                    # TODO: Maybe delete the ban in this case?
                    logging.warn("Could not find (valid) identifier for ban #%s %s", ban_id, identifiers)
                    continue

                expires_at_str = ban_attrs["expires_at"]
                if not expires_at_str:
                    expired = False
                else:
                    expires_at = datetime.fromisoformat(ban_data)
                    expired = expires_at <= datetime.now(tz=timezone.utc)

                responses[ban_id] = BattlemetricsBan(ban_id, player_id, expired)

        return responses


    async def create_ban_list(self, community: schemas.Community):
        data = {
            "data": {
                "type": "banList",
                "attributes": {
                    "name": f"HLL Bunker Ban List - {community.name} (ID: {community.id})",
                    "action": "kick",
                    "defaultIdentifiers": ["steamID"],
                    "defaultReasons": [self.get_ban_reason(community)],
                    "defaultAutoAddEnabled": True
                },
                "relationships": {
                    "organization": {
                        "data": {
                            "type": "organization",
                            "id": self.config.organization_id
                        }
                    },
                    "owner": {
                        "data": {
                            "type": "organization",
                            "id": self.config.organization_id
                        }
                    }
                }
            }
        }

        url = f"{self.BASE_URL}/ban-lists"
        resp = await self._make_request(method="POST", url=url, data=data)

        assert resp["data"]["type"] == "banList"
        self.config.banlist_id = UUID(resp["data"]["id"])

    async def validate_ban_list(self):
        data = {"include": "owner"}

        url = f"{self.BASE_URL}/ban-lists/{self.config.banlist_id}"
        try:
            resp = await self._make_request(method="GET", url=url, data=data)
        except Exception as e:
            raise IntegrationValidationError("Failed to retrieve ban list") from e

        assert resp["data"]["id"] == str(self.config.banlist_id)
        assert resp["data"]["relationships"]["owner"]["data"]["id"] == self.config.organization_id

    async def get_api_scopes(self) -> set[str]:
        """Retrieves the tokens scopes from the oauth.
        Documentation: None.
        Returns:
            dict: The tokens data.
        """
        url = f"https://www.battlemetrics.com/oauth/introspect"
        data = {
            "token": self.config.api_key
        }
        resp = await self._make_request(method="POST", url=url, data=data)

        if resp["active"]:
            return set(resp["scope"].split(" "))
        else:
            # TODO: Create more specific exception class
            raise Exception("Invalid API key")

    async def validate_scopes(self):
        try:
            scopes = await self.get_api_scopes()
        except Exception as e:
            raise IntegrationValidationError("Failed to retrieve API scopes") from e

        required_scopes = {s: False for s in REQUIRED_SCOPES}
        for scope in scopes:
            if scope == "ban" or scope == "ban-list":
                for s in required_scopes:
                    if s.startswith(scope + ":"):
                        required_scopes[s] = True
            elif scope in required_scopes:
                required_scopes[scope] = True

        # TODO: Raise if missing
        if not all(required_scopes.values()):
            raise IntegrationValidationError("Missing scopes: %s" % ", ".join(
                [k for k, v in required_scopes.items() if v is False]
            )) 
