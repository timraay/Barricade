import aiohttp
from uuid import UUID

from bunker import schemas
from bunker.constants import DISCORD_GUILD_ID, DISCORD_REPORTS_CHANNEL_ID
from bunker.services.service import Service
from bunker.utils import get_player_id_type, PlayerIDType

REQUIRED_SCOPES = [
    "ban:create",
    "ban:edit",
    "ban:delete",
    "ban-list:create",
    "ban-list:read"
]

class BattlemetricsService(Service):
    BASE_URL = "https://api.battlemetrics.com"

    def __init__(self, config: schemas.BattlemetricsServiceConfig) -> None:
        super().__init__("Battlemetrics", config)
        self.config: schemas.BattlemetricsServiceConfig

    async def validate(self, community: schemas.Community):
        await self.validate_scopes()

        if not self.config.banlist_id:
            await self.create_ban_list(community)
        else:
            await self.validate_ban_list()        

    async def confirm_report(self, response: schemas.Response):
        reason = self._get_ban_reason(response.community)
        await self.add_ban(
            identifier=response.player_report.player_id,
            reason=reason,
            note=(
                f"Originally reported for {', '.join([reason.reason for reason in response.player_report.report.reasons])}\n"
                f"https://discord.com/channels/{DISCORD_GUILD_ID}/{DISCORD_REPORTS_CHANNEL_ID}/{response.player_report.report.message_id}"
            )
        )


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

    async def add_ban(self, identifier: str, reason: str, note: str):
        data = {
            "data": {
                "type": "ban",
                "attributes": {
                    "autoAddEnabled": True,
                    "expires": None,
                    "identifiers": [
                        {
                            "type": "steamID",
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
                            "id": self.config.banlist_id
                        }
                    }
                }
            }
        }

        url = f"{self.BASE_URL}/bans"
        return await self._make_request(method="POST", url=url, data=data)

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
        self.config.banlist_id = resp["data"]["id"]


    async def validate_ban_list(self):
        data = {"include": "owner"}

        url = f"{self.BASE_URL}/ban-lists/{self.config.banlist_id}"
        resp = await self._make_request(method="GET", url=url, data=data)

        # TODO: Raise properly
        assert resp["data"]["id"] == str(self.config.banlist_id)
        assert resp["relationships"]["owner"]["data"]["id"] == self.config.organization_id

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
