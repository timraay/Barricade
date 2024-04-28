from typing import Sequence
import aiohttp
import logging
import pydantic
from sqlalchemy.ext.asyncio import AsyncSession

from bunker import schemas
from bunker.db import models, session_factory
from bunker.enums import IntegrationType
from bunker.exceptions import (
    IntegrationBanError, IntegrationBulkBanError, NotFoundError,
    AlreadyBannedError, IntegrationValidationError
)
from bunker.integrations.integration import Integration, IntegrationMetaData
from bunker.integrations.websocket import Websocket
from bunker.web.security import generate_token_value, get_token_hash

class DoEnableBunkerApiIntegrationPayload(pydantic.BaseModel):
    community: schemas._CommunityBase
    api_key: str

class DoDisableBunkerApiIntegrationPayload(pydantic.BaseModel):
    community: schemas._CommunityBase
    remove_bans: bool = False

class DoAddBanPayload(pydantic.BaseModel):
    player_id: str
    player_name: str
    reason: str

class DoRemoveBanPayload(pydantic.BaseModel):
    player_id: str

class DoAddMultipleBansPayload(pydantic.BaseModel):
    bans: list[DoAddBanPayload]

class DoRemoveMultipleBansPayload(pydantic.BaseModel):
    player_ids: list[str]

class CRCONIntegration(Integration):
    meta = IntegrationMetaData(
        name="Community RCON",
        config_cls=schemas.CRCONIntegrationConfig,
        type=IntegrationType.COMMUNITY_RCON,
        ask_remove_bans=False,
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

    async def ban_player(self, params: schemas.IntegrationBanPlayerParams):
        async with session_factory.begin() as db:
            db_ban = await self.get_ban(db, params.player_id)
            if db_ban is not None:
                raise AlreadyBannedError(params.player_id, "Player is already banned")

            try:
                await self.add_ban(DoAddBanPayload(
                    player_id=params.player_id,
                    reason=self.get_ban_reason(params.community)
                ))
            except Exception as e:
                raise IntegrationBanError(params.player_id, "Failed to ban player") from e

            await self.set_ban_id(db, params.player_id, params.player_id)

    async def unban_player(self, player_id: str):
        async with session_factory.begin() as db:
            db_ban = await self.get_ban(db, player_id)
            if db_ban is None:
                raise NotFoundError("Ban does not exist")

            try:
                await self.remove_ban(DoRemoveBanPayload(
                    player_id=player_id,
                ))
            except Exception as e:
                raise IntegrationBanError(player_id, "Failed to unban player") from e

            await db.delete(db_ban)
    
    async def bulk_ban_players(self, params: Sequence[schemas.IntegrationBanPlayerParams]):
        try:
            await self.add_multiple_bans(DoAddMultipleBansPayload(bans=[
                DoAddBanPayload(
                    player_id=param.player_id,
                    reason=self.get_ban_reason(param.community)
                )
                for param in params
            ]))
        except Exception as e:
            raise IntegrationBulkBanError(
                [param.player_id for param in params],
                "Failed to ban players"
            ) from e

        async with session_factory.begin() as db:
            await self.set_multiple_ban_ids(db, [
                (param.player_id, param.player_id)
                for param in params
            ])

    async def bulk_unban_players(self, player_ids: Sequence[str]):
        try:
            await self.remove_multiple_bans(DoRemoveMultipleBansPayload(player_ids=player_ids))
        except Exception as e:
            raise IntegrationBulkBanError(player_ids, "Failed to unban players") from e

        async with session_factory.begin() as db:
            await self.discard_multiple_ban_ids(db, player_ids)

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

    async def submit_api_key(self, data: DoEnableBunkerApiIntegrationPayload):
        await self._make_request(method="POST", endpoint="/do_enable_bunker_api_integration", data=data.model_dump())
    
    async def revoke_api_key(self, data: DoDisableBunkerApiIntegrationPayload):
        await self._make_request(method="DELETE", endpoint="/do_disable_bunker_api_integration", data=data.model_dump())

    async def add_ban(self, data: DoAddBanPayload):
        await self._make_request(method="POST", endpoint="/do_add_bunker_blacklist", data=data.model_dump())

    async def remove_ban(self, data: DoRemoveBanPayload):
        await self._make_request(method="DELETE", endpoint="/do_remove_bunker_blacklist", data=data.model_dump())

    async def add_multiple_bans(self, data: DoAddMultipleBansPayload):
        await self._make_request(method="POST", endpoint="/do_add_multiple_bunker_blacklist", data=data.model_dump())

    async def remove_multiple_bans(self, data: DoRemoveMultipleBansPayload):
        await self._make_request(method="DELETE", endpoint="/do_remove_multiple_bunker_blacklist", data=data.model_dump())
