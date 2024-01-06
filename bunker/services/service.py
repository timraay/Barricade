from abc import ABC, abstractmethod
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from bunker import schemas
from bunker.db import models

class Service(ABC):
    def __init__(self, config: schemas.ServiceConfig, display_name: str = "Service"):
        self.display_name = display_name
        self.config = config
    
    async def enable(self, db: AsyncSession, community: Optional[models.Community] = None) -> models.Community:
        self.config.enabled = True
        return await self.save_config(db, community)

    async def disable(self, db: AsyncSession, community: Optional[models.Community] = None) -> models.Community:
        self.config.enabled = False
        return await self.save_config(db, community)

    @abstractmethod
    async def save_config(self, db: AsyncSession, community: Optional[models.Community] = None) -> models.Community:
        raise NotImplementedError
    
    @abstractmethod
    async def validate(self, community: schemas.Community):
        raise NotImplementedError

    @abstractmethod
    async def confirm_report(self, response: schemas.Response):
        raise NotImplementedError
