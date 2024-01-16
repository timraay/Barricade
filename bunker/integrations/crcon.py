from typing import Sequence
import aiohttp
import logging
import pydantic
from sqlalchemy.ext.asyncio import AsyncSession

from bunker import schemas
from bunker.db import models, session_factory
from bunker.exceptions import IntegrationBanError, IntegrationBulkBanError, NotFoundError, AlreadyBannedError
from bunker.integrations.integration import Integration
from bunker.schemas import Response
from bunker.web.security import generate_token_value, get_token_hash

class DoEnableBunkerApiIntegrationPayload(pydantic.BaseModel):
    community: schemas.CommunityBase
    api_key: str

class DoDisableBunkerApiIntegrationPayload(pydantic.BaseModel):
    community: schemas.CommunityBase
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
    def __init__(self, config: schemas.CRCONIntegrationConfigParams) -> None:
        super().__init__(config)
        self.config: schemas.CRCONIntegrationConfigParams

    async def enable(self, db: AsyncSession):
        # Generate token
        token_value = generate_token_value()
        hashed_token_value = get_token_hash(token_value)
        db_token = models.WebToken(
            hashed_token=hashed_token_value,
            scopes=0,
            expires=None,
            user_id=None,
            community_id=self.config.community_id
        )
        db.add(db_token)
        await db.flush()
        await db.refresh(db_token)

        # Submit token
        await self.submit_api_key(self, DoEnableBunkerApiIntegrationPayload(
            community=db_token.community,
            api_key=token_value,
        ))

        # Enable and save
        self.config.bunker_api_key_id = db_token.id
        return await super().enable(db)
    
    async def disable(self, db: AsyncSession, remove_bans: bool) -> models.Integration:
        if self.config.bunker_api_key_id:
            # Delete token
            db_token = await db.get_one(models.WebToken, self.config.bunker_api_key_id)
            db_community = db_token.community
            db.delete(db_token)
            await db.flush()

            # Notify CRCON
            try:
                await self.revoke_api_key(DoDisableBunkerApiIntegrationPayload(
                    community=db_community,
                    remove_bans=remove_bans
                ))
            except:
                logging.error("Failed to notify server of disabled CRCON integration")

        # Disable and save
        return await super().disable(db)

    async def validate(self, community: schemas.Community):
        if community.id != self.config.community_id:
            raise ValueError("Communities do not match")

        if not self.config.api_url.endswith("/api"):
            raise ValueError("API URL does not end with \"/api\"")
        
        await self.validate_api_access()

    async def ban_player(self, response: schemas.Response):
        async with session_factory() as db:
            db_ban = await self.get_ban(db, response)
            if db_ban is not None:
                raise AlreadyBannedError(response, "Player is already banned")

            try:
                await self.add_ban(DoAddBanPayload(
                    player_id=response.player_report.player_id,
                    reason=self.get_ban_reason(response.community)
                ))
            except Exception as e:
                raise IntegrationBanError(response, "Failed to ban player") from e

            await self.set_ban_id(db, response, response.player_report.player_id)

    async def unban_player(self, response: schemas.Response):
        async with session_factory() as db:
            db_ban = await self.get_ban(db, response)
            if db_ban is None:
                raise NotFoundError("Ban does not exist")

            try:
                await self.remove_ban(DoRemoveBanPayload(
                    player_id=response.player_report.player_id,
                ))
            except Exception as e:
                raise IntegrationBanError(response, "Failed to unban player") from e

            await db.delete(db_ban)
            await db.commit()
    
    async def bulk_ban_players(self, responses: Sequence[Response]):
        try:
            await self.add_multiple_bans(DoAddMultipleBansPayload(bans=[
                DoAddBanPayload(
                    player_id=response.player_report.player_id,
                    reason=self.get_ban_reason(response.community)
                )
                for response in responses
            ]))
        except Exception as e:
            raise IntegrationBulkBanError(responses, "Failed to ban players") from e

        async with session_factory() as db:
            await self.set_multiple_ban_ids(db, [
                (response, response.player_report.player_id)
                for response in responses
            ])

    async def bulk_unban_players(self, responses: Sequence[Response]):
        try:
            await self.remove_multiple_bans(DoRemoveMultipleBansPayload(player_ids=[
                response.player_report.player_id
                for response in responses
            ]))
        except Exception as e:
            raise IntegrationBulkBanError(responses, "Failed to unban players") from e

        async with session_factory() as db:
            await self.discard_multiple_ban_ids(db, responses)

    
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
        resp = await self._make_request(method="GET", endpoint="/is_logged_in")
        is_auth = resp.get("authenticated")
        if is_auth is False:
            raise ValueError("Invalid API key")
        elif is_auth is not True:
            raise ValueError("Received unexpected API response")

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
