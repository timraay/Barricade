import aiohttp
from uuid import UUID
from sqlalchemy import update

from bunker import schemas
from bunker.constants import DISCORD_GUILD_ID, DISCORD_REPORTS_CHANNEL_ID
from bunker.db import models, session_factory
from bunker.integrations.integration import Integration
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

    async def get_instance_name(self) -> str:
        return "NAME"

    async def validate(self, community: schemas.Community):
        if community.id != self.config.community_id:
            raise ValueError("Communities do not match")

        await self.validate_scopes()

        if not self.config.banlist_id:
            await self.create_ban_list(community)
        else:
            await self.validate_ban_list()        

    async def ban_player(self, response: schemas.Response):
        if response.bm_ban_id:
            return

        reason = self._get_ban_reason(response.community)
        ban_id = await self.add_ban(
            identifier=response.player_report.player_id,
            reason=reason,
            note=(
                f"Originally reported for {', '.join([reason.reason for reason in response.player_report.report.reasons])}\n"
                f"https://discord.com/channels/{DISCORD_GUILD_ID}/{DISCORD_REPORTS_CHANNEL_ID}/{response.player_report.report.message_id}"
            )
        )
        async with session_factory() as db:
            stmt = update(schemas.Response).values(bm_ban_id=ban_id).where(
                models.PlayerReportResponse.pr_id==response.pr_id,
                models.PlayerReportResponse.community_id==response.community.id
            )
            await db.execute(stmt)
            await db.commit()

    async def unban_player(self, response: schemas.Response):
        if not response.bm_ban_id:
            return

        await self.remove_ban(response.bm_ban_id)
        async with session_factory() as db:
            stmt = update(schemas.Response).values(bm_ban_id=response.bm_ban_id).where(
                models.PlayerReportResponse.pr_id==response.pr_id,
                models.PlayerReportResponse.community_id==response.community.id
            )
            await db.execute(stmt)
            await db.commit()


    def _get_ban_reason(self, community: schemas.Community) -> str:
        return (
            "Banned via shared HLL Bunker report. Appeal"
            f" at {community.contact_url}"
        )

    async def _make_request(self, method: str, url: str, data: dict = None) -> dict:
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
                else:
                    raise Exception(f"Unsupported content type: {content_type}")

        return response 


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

    async def remove_ban(self, ban_id: str) -> str:
        url = f"{self.BASE_URL}/bans/{ban_id}"
        await self._make_request(method="DELETE", url=url)

    async def create_ban_list(self, community: schemas.Community):
        data = {
            "data": {
                "type": "banList",
                "attributes": {
                    "name": f"HLL Bunker Ban List - {community.name} (ID: {community.id})",
                    "action": "kick",
                    "defaultIdentifiers": ["steamID"],
                    "defaultReasons": [self._get_ban_reason(community)],
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

    async def get_ban_list_bans(self) -> list:
        data = {
            "filter[banList]": str(self.config.banlist_id),
            "page[size]": 100,
            "filter[expired]": "true"
        }

        url = f"{self.BASE_URL}/bans"
        resp = await self._make_request(method="GET", url=url, data=data)
        responses: list = resp["data"]

        # while resp["links"].get("next"):
        #     resp = await self._make_request(method="GET", url=resp["links"]["next"])
        #     responses.extend(resp["data"])

        return responses


    async def validate_ban_list(self):
        data = {"include": "owner"}

        url = f"{self.BASE_URL}/ban-lists/{self.config.banlist_id}"
        resp = await self._make_request(method="GET", url=url, data=data)

        # TODO: Raise properly
        assert resp["data"]["id"] == str(self.config.banlist_id)
        assert resp["data"]["relationships"]["owner"]["data"]["id"] == self.config.organization_id

    async def validate_scopes(self):
        scopes = await self.get_api_scopes()
        required_scopes = {s: False for s in REQUIRED_SCOPES}
        for scope in scopes:
            if scope == "ban" or scope == "ban-list":
                for s in required_scopes:
                    if s.startswith(scope + ":"):
                        required_scopes[s] = True
            elif scope in required_scopes:
                required_scopes[scope] = True

        # TODO: Raise if missing
        return all(required_scopes.values())
