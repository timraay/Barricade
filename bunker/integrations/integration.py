from abc import ABC, abstractmethod
from sqlalchemy.ext.asyncio import AsyncSession

from bunker import schemas
from bunker.communities import create_integration_config, update_integration_config
from bunker.exceptions import NotFoundError
from bunker.db import models

class Integration(ABC):
    def __init__(self, config: schemas.IntegrationConfigBase):
        self.config = config
    
    async def enable(self, db: AsyncSession) -> models.Integration:
        """Enable this integration.

        Updates and saves the config.

        Parameters
        ----------
        db : AsyncSession
            An asynchronous database session

        Returns
        -------
        models.Integration
            The integration config record
        """
        self.config.enabled = True
        return await self.save_config(db)

    async def disable(self, db: AsyncSession, remove_bans: bool) -> models.Integration:
        """Disable this integration.

        Updates and saves the config.

        Parameters
        ----------
        db : AsyncSession
            An asynchronous database session
        remove_bans : bool
            Whether to remove all bans

        Returns
        -------
        models.Integration
            The integration config record
        """
        self.config.enabled = False
        return await self.save_config(db)

    async def save_config(self, db: AsyncSession) -> models.Integration:
        """Save the integration's config.

        Parameters
        ----------
        db : AsyncSession
            An asynchronous database session

        Returns
        -------
        models.Integration
            The integration config record
        """
        if self.config.id is None:
            return await create_integration_config(db, self.config)
        else:
            return await update_integration_config(db, self.config)
    
    async def set_ban_id(self, db: AsyncSession, response: schemas.Response, ban_id: str) -> models.PlayerBan:
        db_ban = models.PlayerBan(
            prr_id=response.id,
            integration_id=self.config.id,
            remote_id=ban_id,
        )
        db.add(db_ban)
        await db.commit()
        return db_ban
    
    async def discard_ban_id(self, db: AsyncSession, response: schemas.Response):
        db_ban = await db.get(models.PlayerBan, (response.pr_id, self.config.id))
        if not db_ban:
            raise NotFoundError("Ban does not exist")
        await db.delete(db_ban)
        await db.commit()
    
    def get_ban_reason(self, community: schemas.Community) -> str:
        return (
            "Banned via shared HLL Bunker report. Appeal"
            f" at {community.contact_url}"
        )

    @abstractmethod
    async def get_instance_name(self) -> str:
        """Fetch the name of the specific instance that this
        integration connects to. Ideally this is cached.

        Returns
        -------
        str
            The name of the connected instance.
        """
        raise NotImplementedError

    @abstractmethod
    async def validate(self, community: schemas.Community):
        """Validate the integration's config.

        Parameters
        ----------
        community : models.Community
            The community owning this integration

        Raises
        ------
        Exception
            A config value is incorrect or outdated
        """
        raise NotImplementedError

    @abstractmethod
    async def ban_player(self, response: schemas.Response):
        """Instruct the remote integration to ban a player.

        Parameters
        ----------
        response : schemas.Response
            The community's response to a rapported player

        Raises
        ------
        Exception
            Failed to ban the player.
        """
        raise NotImplementedError

    @abstractmethod
    async def unban_player(self, response: schemas.Response):
        """Instruct the remote integration to unban a player, should
        they be banned.

        Parameters
        ----------
        response : schemas.Response
            The community's response to a rapported player

        Raises
        ------
        Exception
            Failed to unban the player.
        """
        raise NotImplementedError
