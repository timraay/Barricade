from datetime import datetime, timezone
import logging
from typing import Sequence, NamedTuple
import aiohttp
from discord import Embed

from barricade import schemas
from barricade.constants import DISCORD_GUILD_ID
from barricade.crud.bans import expire_bans_of_player, get_bans_by_integration
from barricade.crud.communities import get_community_by_id
from barricade.db import session_factory
from barricade.discord.communities import safe_send_to_community
from barricade.discord.reports import get_report_channel
from barricade.discord.utils import get_danger_embed
from barricade.enums import Emojis, IntegrationType, PlayerIDType
from barricade.exceptions import IntegrationBanError, IntegrationBulkBanError, NotFoundError, IntegrationValidationError
from barricade.integrations.integration import Integration, IntegrationMetaData, is_enabled
from barricade.schemas import Response
from barricade.utils import get_player_id_type, safe_create_task

REQUIRED_SCOPES = [
    "ban:create",
    "ban:edit",
    "ban:delete",
    "ban-list:create",
    "ban-list:read"
]

class BattlemetricsBan(NamedTuple):
    ban_id: str
    player_id: int | None
    expired: bool

class BattlemetricsIntegration(Integration):
    BASE_URL = "https://api.battlemetrics.com"

    meta = IntegrationMetaData(
        name="Battlemetrics",
        config_cls=schemas.BattlemetricsIntegrationConfig,
        type=IntegrationType.BATTLEMETRICS,
        emoji=Emojis.BATTLEMETRICS,
    )

    def __init__(self, config: schemas.BattlemetricsIntegrationConfigParams) -> None:
        super().__init__(config)
        self.config: schemas.BattlemetricsIntegrationConfigParams

    # --- Abstract method implementations

    async def get_instance_name(self) -> str:
        url = f"{self.BASE_URL}/organizations/{self.config.organization_id}"
        resp: dict = await self._make_request(method="GET", url=url) # type: ignore
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
            ban_id = await self.add_ban(
                identifier=player_id,
                reason=reason,
                note=note,
            )
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
                    logging.error("Battlemetrics Ban with ID %s for player %s not found", db_ban.remote_id, player_id)
                else:
                    raise IntegrationBanError(player_id, "Failed to unban player")
            except:
                raise IntegrationBanError(player_id, "Failed to unban player")

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
                except IntegrationBanError:
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
                except IntegrationBanError:
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
                logging.warn("Ban exists on the remote but not locally, expiring: %r", remote_ban)
                await self.expire_ban(remote_ban.ban_id)
                safe_send_to_community(community, embed=embed)

    # --- Battlemetrics API wrappers

    async def _make_request(self, method: str, url: str, data: dict | None = None) -> dict | None:
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
                r.raise_for_status()
                content_type = r.headers.get('content-type', '')

                response: dict | None
                if 'json' in content_type:
                    response = await r.json()
                # elif "text/html" in content_type:
                #     response = (await r.content.read()).decode()
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
        resp: dict = await self._make_request(method="POST", url=url, data=data) # type: ignore

        return resp["data"]["id"]

    async def remove_ban(self, ban_id: str):
        url = f"{self.BASE_URL}/bans/{ban_id}"
        await self._make_request(method="DELETE", url=url)

    async def expire_ban(self, ban_id: str):
        url = f"{self.BASE_URL}/bans/{ban_id}"
        await self._make_request(method="PATCH", url=url, data={
            "data": {
                "type": "ban",
                "attributes": {
                    "expires": datetime.now(tz=timezone.utc)
                }
            }
        })

    async def get_ban_list_bans(self) -> dict[str, BattlemetricsBan]:
        data = {
            "filter[banList]": str(self.config.banlist_id),
            "page[size]": 100,
            "filter[expired]": "false"
        }

        url = f"{self.BASE_URL}/bans"
        resp: dict = await self._make_request(method="GET", url=url, data=data) # type: ignore
        responses = {}

        while resp["links"].get("next"):
            resp: dict = await self._make_request(method="GET", url=resp["links"]["next"]) # type: ignore # type: ignore
            for ban_data in resp["data"]:
                ban_attrs = ban_data["attributes"]

                ban_id = str(ban_data["id"])
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

                expires_at_str = ban_attrs["expires_at"]
                if not expires_at_str:
                    expired = False
                else:
                    expires_at = datetime.fromisoformat(ban_data)
                    expired = expires_at <= datetime.now(tz=timezone.utc)
                
                # If no valid identifier is found, remove remote ban and skip
                if not player_id:
                    logging.warn("Could not find (valid) identifier for ban #%s %s", ban_id, identifiers)
                    responses[ban_id] = BattlemetricsBan(ban_id, None, expired)
                    safe_create_task(
                        self.remove_ban(ban_id),
                        "Failed to remove ban %s with unknown player ID" % ban_id
                    )
                    continue

                responses[ban_id] = BattlemetricsBan(ban_id, player_id, expired)

        return responses


    async def create_ban_list(self, community: schemas.Community):
        data = {
            "data": {
                "type": "banList",
                "attributes": {
                    "name": f"HLL Barricade - {community.name} (ID: {community.id})",
                    "action": "kick",
                    "defaultIdentifiers": ["steamID"],
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

        url = f"{self.BASE_URL}/ban-lists"
        resp: dict = await self._make_request(method="POST", url=url, data=data) # type: ignore

        assert resp["data"]["type"] == "banList"
        self.config.banlist_id = resp["data"]["id"]

    async def validate_ban_list(self):
        data = {"include": "owner"}

        url = f"{self.BASE_URL}/ban-lists/{self.config.banlist_id}"
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
