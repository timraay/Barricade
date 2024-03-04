import logging
from typing import Sequence
import aiohttp
from uuid import UUID

from bunker import schemas
from bunker.constants import DISCORD_GUILD_ID, DISCORD_REPORTS_CHANNEL_ID
from bunker.db import session_factory
from bunker.exceptions import IntegrationBanError, IntegrationBulkBanError, NotFoundError, IntegrationValidationError
from bunker.integrations.integration import Integration
from bunker.schemas import Response
from bunker.utils import get_player_id_type

REQUIRED_SCOPES = [
    "ban:create",
    "ban:edit",
    "ban:delete",
    "ban-list:create",
    "ban-list:read"
]

class BattlemetricsIntegration(Integration):
    BASE_URL = "https://api.battlemetrics.com"

    def __init__(self, config: schemas.BattlemetricsIntegrationConfigParams) -> None:
        super().__init__(config)
        self.config: schemas.BattlemetricsIntegrationConfigParams

    # --- Abstract method implementations

    async def get_instance_name(self) -> str:
        url = f"{self.BASE_URL}/organizations/{self.config.organization_id}"
        resp = await self._make_request(method="GET", url=url)
        return resp["data"]["attributes"]["name"]

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
        async with session_factory() as db:
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
        async with session_factory() as db:
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
            await db.commit()

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
        
        async with session_factory() as db:
            await self.set_multiple_ban_ids(db)
        
        if failed:
            raise IntegrationBulkBanError(failed, "Failed to ban players")

    async def bulk_unban_players(self, responses: Sequence[Response]):
        failed = []
        async with session_factory() as db:
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
        
            await db.commit()
        
        if failed:
            raise IntegrationBulkBanError(failed, "Failed to unban players")

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

    async def get_ban_list_bans(self) -> list:
        data = {
            "filter[banList]": str(self.config.banlist_id),
            "page[size]": 100,
            "filter[expired]": "true"
        }

        url = f"{self.BASE_URL}/bans"
        resp = await self._make_request(method="GET", url=url, data=data)
        responses: list = resp["data"]

        while resp["links"].get("next"):
            resp = await self._make_request(method="GET", url=resp["links"]["next"])
            responses.extend(resp["data"])

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
