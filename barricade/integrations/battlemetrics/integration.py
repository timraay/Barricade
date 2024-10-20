from datetime import datetime, timezone
import itertools
from typing import Sequence, NamedTuple
from uuid import uuid4
import aiohttp

from barricade import schemas
from barricade.crud.bans import expire_bans_of_player, get_bans_by_integration
from barricade.crud.communities import get_community_by_id
from barricade.db import session_factory
from barricade.discord.communities import safe_send_to_community
from barricade.discord.reports import get_report_channel
from barricade.discord.utils import get_danger_embed
from barricade.enums import Emojis, IntegrationType
from barricade.exceptions import IntegrationBanError, IntegrationBulkBanError, IntegrationFailureError, IntegrationMissingPermissionsError, NotFoundError, IntegrationValidationError
from barricade.integrations.battlemetrics.utils import find_player_id_in_attributes
from barricade.integrations.battlemetrics.websocket import BattlemetricsWebsocket
from barricade.integrations.integration import Integration, IntegrationMetaData, is_enabled
from barricade.utils import batched, get_player_id_type, safe_create_task, async_ttl_cache

REQUIRED_SCOPES = {
    "ban:create",
    "ban:update",
    "ban:delete",
    "ban:read",
    "ban-list:create",
    "ban-list:read",
    "rcon:read",
}

OPTIONAL_SCOPES = {
    "trigger:create",
    "trigger:read",
}

class BattlemetricsBan(NamedTuple):
    ban_id: str
    player_id: str | None
    expired: bool
    has_player_linked: bool

class BattlemetricsIntegration(Integration):
    BASE_API_URL = "https://api.battlemetrics.com"
    BASE_WS_URL = "wss://ws.battlemetrics.com?audit_log=id={}"

    meta = IntegrationMetaData(
        name="Battlemetrics",
        config_cls=schemas.BattlemetricsIntegrationConfig,
        type=IntegrationType.BATTLEMETRICS,
        emoji=Emojis.BATTLEMETRICS,
    )

    def __init__(self, config: schemas.BattlemetricsIntegrationConfigParams) -> None:
        super().__init__(config)
        self.config: schemas.BattlemetricsIntegrationConfigParams
        self.ws = BattlemetricsWebsocket(self)

    def get_ws_url(self):
        return self.BASE_WS_URL.format(uuid4())

    # --- Extended parent methods

    def start_connection(self):
        self.ws.start()
    
    def stop_connection(self):
        self.ws.stop()
    
    def update_connection(self):
        self.ws.address = self.get_ws_url()
        self.ws.token = self.config.api_key
        if self.config.enabled:
            self.ws.update_connection()

    # TODO: Extend on_report_create to send alerts if a newly reported player is currently online

    def get_ban_reason(self, response: schemas.ResponseWithToken) -> str:
        # BM imposes a character limit of 255 characters, so we want to make the
        # message a little more concise:
        # - Shorten the title
        # - Use community tags instead of names
        # - Remove "https://" from URLs
        # If that is not enough, any characters that won't fit will be stripped from
        # the reason.
        report = response.player_report.report
        reporting_community = report.token.community
        message = (
            f"Reported by {reporting_community.tag}\n"
            f"Contact: {reporting_community.contact_url}"
            "\n\n"
            f"Banned by {response.community.tag}\n"
            f"Contact: {response.community.contact_url}"
            "\n\n"
            "More info: bit.ly/BarricadeBanned"
        ).replace("https://", "")

        max_reasons_len = 255 - 27 - len(message) # remaining = max - title - msg
        reasons = ', '.join(report.reasons_bitflag.to_list(report.reasons_custom))
        if len(reasons) > max_reasons_len:
            reasons = reasons[:max_reasons_len - 2] + ".."

        return f"HLL Barricade banned for {reasons}\n\n{message}"

    # --- Abstract method implementations

    @async_ttl_cache(size=9999, seconds=60*10)
    async def get_instance_name(self) -> str:
        url = f"{self.BASE_API_URL}/organizations/{self.config.organization_id}"
        resp: dict = await self._make_request(method="GET", url=url) # type: ignore
        return resp["data"]["attributes"]["name"]
    
    def get_instance_url(self) -> str:
        return f"https://battlemetrics.com/rcon/orgs/{self.config.organization_id}/edit"

    async def validate(self, community: schemas.Community) -> set[str]:
        if community.id != self.config.community_id:
            raise IntegrationValidationError("Communities do not match")

        missing_optional_scopes = await self.validate_scopes()

        if not self.config.banlist_id:
            try:
                await self.create_ban_list(community)
            except Exception as e:
                raise IntegrationValidationError("Failed to create ban list") from e
        else:
            await self.validate_ban_list()
        
        return missing_optional_scopes

    @is_enabled
    async def ban_player(self, response: schemas.ResponseWithToken):
        player_id = response.player_report.player_id
        report = response.player_report.report
        report_channel = get_report_channel(report.token.platform)

        async with session_factory.begin() as db:
            db_ban = await self.get_ban(db, player_id)
            if db_ban is not None:
                raise IntegrationBanError(player_id, "Player is already banned")

            reason = self.get_ban_reason(response)
            note = (
                f"Banned for {', '.join(report.reasons_bitflag.to_list(report.reasons_custom))}.\n"
                f"Reported by {report.token.community.name} ({report.token.community.contact_url})\n"
                f"Link to Bunker message: {report_channel.jump_url}/{report.message_id}"
            )
            
            try:
                ban_id = await self.add_ban(
                    identifier=player_id,
                    reason=reason,
                    note=note,
                )
            except IntegrationFailureError:
                raise
            except Exception as e:
                raise IntegrationBanError(player_id, "Failed to ban player") from e
            
            await self.set_ban_id(db, player_id, ban_id)

    @is_enabled
    async def unban_player(self, player_id: str):
        async with session_factory.begin() as db:
            db_ban = await self.get_ban(db, player_id)
            if db_ban is None:
                raise NotFoundError("Ban does not exist")

            try:
                await self.remove_ban(db_ban.remote_id)
            except aiohttp.ClientResponseError as e:
                if e.status == 404:
                    self.logger.error("Battlemetrics Ban with ID %s for player %s not found", db_ban.remote_id, player_id)
                else:
                    raise IntegrationBanError(player_id, "Failed to unban player")
            except IntegrationFailureError:
                raise
            except Exception as e:
                raise IntegrationBanError(player_id, "Failed to unban player") from e

            await db.delete(db_ban)

    @is_enabled
    async def bulk_ban_players(self, responses: Sequence[schemas.ResponseWithToken]):
        ban_ids = []
        failed = []
        async with session_factory.begin() as db:
            for response in responses:
                player_id = response.player_report.player_id
                report = response.player_report.report
                report_channel = get_report_channel(report.token.platform)

                db_ban = await self.get_ban(db, player_id)
                if db_ban is not None:
                    continue

                reason = self.get_ban_reason(response)
                note = (
                    f"Banned for {', '.join(report.reasons_bitflag.to_list(report.reasons_custom))}.\n"
                    f"Reported by {report.token.community.name} ({report.token.community.contact_url})\n"
                    f"Link to Bunker message: {report_channel.jump_url}/{report.message_id}"
                )
                try:
                    ban_id = await self.add_ban(
                        identifier=player_id,
                        reason=reason,
                        note=note,
                    )
                except IntegrationFailureError:
                    failed.append(player_id)
                else:
                    ban_ids.append((player_id, ban_id))
        
            await self.set_multiple_ban_ids(db, *ban_ids)
        
        if failed:
            raise IntegrationBulkBanError(failed, "Failed to ban players")

    @is_enabled
    async def bulk_unban_players(self, player_ids: Sequence[str]):
        failed = []
        async with session_factory.begin() as db:
            for player_id in player_ids:
                db_ban = await self.get_ban(db, player_id)
                if not db_ban:
                    continue

                try:
                    await self.remove_ban(db_ban.remote_id)
                except IntegrationFailureError:
                    failed.append(player_id)
                else:
                    await db.delete(db_ban)
        
        if failed:
            raise IntegrationBulkBanError(failed, "Failed to unban players")
    
    @is_enabled
    async def synchronize(self):
        if not self.config.id:
            raise RuntimeError("Integration has not yet been saved")

        remote_bans = await self.get_ban_list_bans()
        unlinked_bans = [ban for ban in remote_bans.values() if not ban.has_player_linked]

        async with session_factory.begin() as db:
            db_community = await get_community_by_id(db, self.config.community_id)
            community = schemas.CommunityRef.model_validate(db_community)

            async for db_ban in get_bans_by_integration(db, self.config.id):
                remote_ban = remote_bans.pop(db_ban.remote_id, None)
                if not remote_ban:
                    await db.delete(db_ban)

                elif remote_ban.expired:
                    # The player was unbanned, change responses of all reports where
                    # the player is banned
                    async with session_factory.begin() as _db:
                        await expire_bans_of_player(_db, db_ban.player_id, db_ban.integration.community_id)
            
            for remote_ban in remote_bans.values():
                if remote_ban.expired:
                    continue

                embed = get_danger_embed(
                    "Found unrecognized ban on Battlemetrics ban list!",
                    (
                        f"-# Your Barricade ban list contained [an active ban](https://battlemetrics.com/rcon/bans/{remote_ban.ban_id}) that Barricade does not recognize."
                        " Please do not put any of your own bans on this ban list."
                        "\n\n"
                        "-# The ban has been expired. If you wish to restore it, move it to a different ban list first. If this is a Barricade ban, feel free to ignore this."
                    )
                )
                self.logger.warn("Ban exists on the remote but not locally, expiring: %r", remote_ban)
                await self.expire_ban(remote_ban.ban_id)
                safe_send_to_community(community, embed=embed)

        await self.link_bans_to_players(unlinked_bans)

    # --- Battlemetrics API wrappers

    async def _make_request(self, method: str, url: str, data: dict | None = None) -> dict | str | None:
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

            async with session.request(method=method, url=url, **kwargs) as r: # type: ignore
                content_type = r.headers.get('content-type', '')
                response: dict | str | None
                if "json" in content_type:
                    response = await r.json()
                elif "text/html" in content_type:
                    response = (await r.content.read()).decode()
                elif not content_type:
                    response = None
                else:
                    raise Exception(f"Unsupported content type: {content_type}")
                
                if not r.ok:
                    self.logger.error(
                        "Failed request %s %s. Data = %s, Response = %s",
                        method, url, kwargs, response
                    )
                    r.raise_for_status()

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

        url = f"{self.BASE_API_URL}/bans"
        resp: dict = await self._make_request(method="POST", url=url, data=data) # type: ignore

        return resp["data"]["id"]

    async def edit_ban(self, remote_id: str, player_bm_id: str, identifier_id: str):
        data = {
            "data": {
                "type": "ban",
                "attributes": {
                    "identifiers": [
                        {
                            "id": identifier_id
                        }
                    ],
                },
                "relationships": {
                    "player": {
                        "data": {
                            "type": "player",
                            "id": {player_bm_id}
                        }
                    }
                }
            }
        }

        url = f"{self.BASE_API_URL}/bans/{remote_id}"
        resp: dict = await self._make_request(method="POST", url=url, data=data) # type: ignore

        return resp["data"]["id"]

    async def remove_ban(self, ban_id: str):
        url = f"{self.BASE_API_URL}/bans/{ban_id}"
        await self._make_request(method="DELETE", url=url)

    async def expire_ban(self, ban_id: str):
        url = f"{self.BASE_API_URL}/bans/{ban_id}"
        await self._make_request(method="PATCH", url=url, data={
            "data": {
                "type": "ban",
                "attributes": {
                    "expires": datetime.now(tz=timezone.utc).isoformat()
                }
            }
        })

    async def get_ban_list_bans(self) -> dict[str, BattlemetricsBan]:
        data = {
            "filter[banList]": str(self.config.banlist_id),
            "page[size]": 100,
            "filter[expired]": "false"
        }

        url = f"{self.BASE_API_URL}/bans"
        resp: dict = await self._make_request(method="GET", url=url, data=data) # type: ignore
        responses = {}

        while resp["links"].get("next"):
            resp: dict = await self._make_request(method="GET", url=resp["links"]["next"]) # type: ignore # type: ignore
            for ban_data in resp["data"]:
                ban_attrs = ban_data["attributes"]
                has_player_linked = ban_data["relationships"].get("player") is not None

                ban_id = str(ban_data["id"])

                # Find identifier of valid type
                player_id, _ = find_player_id_in_attributes(ban_attrs)

                expires_at_str = ban_attrs["expires_at"]
                if not expires_at_str:
                    expired = False
                else:
                    expires_at = datetime.fromisoformat(ban_data)
                    expired = expires_at <= datetime.now(tz=timezone.utc)
                
                # If no valid identifier is found, remove remote ban and skip
                if not player_id:
                    self.logger.warning("Could not find (valid) identifier for ban #%s", ban_id, ban_attrs["identifiers"])
                    responses[ban_id] = BattlemetricsBan(ban_id, None, expired, True)
                    safe_create_task(
                        self.remove_ban(ban_id),
                        "Failed to remove ban %s with unknown player ID" % ban_id
                    )
                    continue

                responses[ban_id] = BattlemetricsBan(ban_id, player_id, expired, has_player_linked)

        return responses

    async def link_bans_to_players(self, bans: list[BattlemetricsBan]):
        url = f"{self.BASE_API_URL}/players/match"

        for grouped_bans in batched(bans, n=100):
            # Player Match Identifiers endpoint accepts up to 100 IDs at
            # once and has a rate limit of five requests per second.
            query_data = []
            for ban in grouped_bans:
                if ban.player_id is None:
                    # In normal circumstances should not happen but you never know
                    continue

                query_data.append({
                    "type": "identifier",
                    "attributes": {
                        "type": get_player_id_type(ban.player_id).value,
                        "identifier": ban.player_id,
                    }
                })
            
            resp: dict = await self._make_request(
                method="GET",
                url=url,
                data={ "data": query_data },
            ) # type: ignore

            identifier_to_ban_id = {
                ban.player_id: ban.ban_id
                for ban in grouped_bans
            }
            for player_data in resp["data"]:
                assert player_data["type"] == "identifier"
                identifier = player_data["attributes"]["identifier"]
                identifier_id = player_data["id"]
                player_bm_id = player_data["relationships"]["player"]["data"]["id"]
                remote_id = identifier_to_ban_id[identifier]

                await self.edit_ban(remote_id, player_bm_id, identifier_id)


    async def create_ban_list(self, community: schemas.Community):
        data = {
            "data": {
                "type": "banList",
                "attributes": {
                    "name": f"HLL Barricade - {community.name} (ID: {community.id})",
                    "action": "kick",
                    "defaultIdentifiers": ["steamID", "hllWindowsID"],
                    "defaultReasons": [],
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

        url = f"{self.BASE_API_URL}/ban-lists"
        resp: dict = await self._make_request(method="POST", url=url, data=data) # type: ignore

        assert resp["data"]["type"] == "banList"
        self.config.banlist_id = resp["data"]["id"]

    async def validate_ban_list(self):
        data = {"include": "owner"}

        url = f"{self.BASE_API_URL}/ban-lists/{self.config.banlist_id}"
        try:
            resp: dict = await self._make_request(method="GET", url=url, data=data) # type: ignore
        except Exception as e:
            raise IntegrationValidationError("Failed to retrieve ban list") from e

        banlist_id = resp["data"]["id"]
        if banlist_id != self.config.banlist_id:
            raise IntegrationValidationError("Ban list UUID mismatch: Asked for %s but got %s", self.config.banlist_id, resp["data"]["id"])
        
        organization_id = resp["data"]["relationships"]["owner"]["data"]["id"]
        if organization_id != self.config.organization_id:
            raise IntegrationValidationError("Organization ID mismatch: Asked for %s but got %s", self.config.organization_id, resp["data"]["id"])

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
        resp: dict = await self._make_request(method="POST", url=url, data=data) # type: ignore

        if resp["active"]:
            return set(resp["scope"].split(" "))
        else:
            # TODO: Create more specific exception class
            raise Exception("Invalid API key")

    async def validate_scopes(self) -> set[str]:
        try:
            scopes = await self.get_api_scopes()
        except Exception as e:
            raise IntegrationValidationError("Failed to retrieve API scopes") from e

        expected_scopes = {s: False for s in itertools.chain(REQUIRED_SCOPES, OPTIONAL_SCOPES)}
        for scope in scopes:
            if scope in expected_scopes:
                expected_scopes[scope] = True
            else:
                for s in expected_scopes:
                    if s.startswith(scope + ":"):
                        expected_scopes[s] = True

        missing_scopes = {scope for scope, v in expected_scopes.items() if not v}
        missing_required_scopes = missing_scopes & REQUIRED_SCOPES
        missing_optional_scopes = missing_scopes & OPTIONAL_SCOPES
        
        if missing_required_scopes:
            raise IntegrationMissingPermissionsError(
                missing_required_scopes,
                "Missing scopes: %s" % ", ".join(
                    [k for k, v in expected_scopes.items() if v is False]
                )
            )
        
        return missing_optional_scopes

    @async_ttl_cache(size=9999, seconds=60*60*24)
    async def get_server_ids_from_org(self) -> list[str]:
        data = {
            "filter[organizations]": self.config.organization_id,
            "filter[rcon]": "true",
            "filter[game]": "hll"
        }

        url = f"{self.BASE_API_URL}/servers"
        resp: dict = await self._make_request(method="GET", url=url, data=data) # type: ignore

        return [server["id"] for server in resp["data"]]
