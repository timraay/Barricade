import aiohttp
import pydantic
from sqlalchemy.ext.asyncio import AsyncSession

from bunker import schemas
from bunker.communities import get_community_by_id
from bunker.db import models
from bunker.services.service import Service
from bunker.web.security import create_token


class DoEnableBunkerApiIntegrationPayload(pydantic.BaseModel):
    api_key: str
    community: schemas.CommunityBase

class DoDisableBunkerApiIntegrationPayload(pydantic.BaseModel):
    remove_bans: bool = False


class CRCONService(Service):
    def __init__(self, config: schemas.CRCONServiceConfigParams) -> None:
        super().__init__(config)
        self.config: schemas.CRCONServiceConfigParams

    async def enable(self, db: AsyncSession, community: models.Community | None = None) -> models.Community:
        if not community:
            community = await get_community_by_id(db, self.config.community_id)
        
        # db_token, token = await create_token(db, scopes=0, community=community, expires_delta=None)

        return await super().enable(db, community)

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

    async def submit_api_key(self):
        # TODO: Generate actual key
        api_key = "gabagool"

        data = DoEnableBunkerApiIntegrationPayload

        await self._make_request(method="POST", endpoint="/do_enable_bunker_api_integration")