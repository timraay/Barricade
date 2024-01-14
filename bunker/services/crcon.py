import aiohttp
import logging
import pydantic
from sqlalchemy.ext.asyncio import AsyncSession

from bunker import schemas
from bunker.db import models
from bunker.services.service import Service
from bunker.web.security import generate_token_value, get_token_hash

class DoEnableBunkerApiIntegrationPayload(pydantic.BaseModel):
    community: schemas.CommunityBase
    api_key: str

class DoDisableBunkerApiIntegrationPayload(pydantic.BaseModel):
    community: schemas.CommunityBase
    remove_bans: bool = False


class CRCONService(Service):
    def __init__(self, config: schemas.CRCONServiceConfigParams) -> None:
        super().__init__(config)
        self.config: schemas.CRCONServiceConfigParams

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
    
    async def disable(self, db: AsyncSession) -> models.Service:
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
                    remove_bans=False
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
        pass

    async def unban_player(self, response: schemas.Response):
        pass

    
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
        await self._make_request(method="POST", endpoint="/do_enable_bunker_api_integration", data=data)
    
    async def revoke_api_key(self, data: DoDisableBunkerApiIntegrationPayload):
        await self._make_request(method="POST", endpoint="/do_enable_bunker_api_integration", data=data)

