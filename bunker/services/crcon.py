from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from bunker import schemas
from bunker.communities import set_crcon_service
from bunker.db import models
from bunker.services.service import Service

class CRCONService(Service):
    def __init__(self, config: schemas.CRCONServiceConfig) -> None:
        super().__init__(config, "Community RCON")
        self.config: schemas.CRCONServiceConfig

    async def save_config(self, db: AsyncSession, community: Optional[models.Community] = None):
        return await set_crcon_service(db, self.config, community)

    async def validate(self, community: schemas.Community):
        pass

    async def confirm_report(self, response: schemas.Response):
        pass
