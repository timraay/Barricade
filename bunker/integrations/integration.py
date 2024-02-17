from abc import ABC, abstractmethod
from functools import wraps
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Sequence

from bunker import schemas
from bunker.crud.bans import get_ban_by_player_and_integration, create_ban, bulk_create_bans, bulk_delete_bans
from bunker.crud.communities import create_integration_config, update_integration_config
from bunker.exceptions import NotFoundError, AlreadyBannedError
from bunker.db import models

def is_saved(func):
    @wraps(func)
    async def decorator(integration: 'Integration', *args, **kwargs):
        if integration.config.id is None:
            raise RuntimeError("Integration is not saved")
        return await func(integration, *args, **kwargs)
    return decorator

class Integration(ABC):
    def __init__(self, config: schemas._IntegrationConfigBase):
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
            db_config = await create_integration_config(db, self.config)
        else:
            db_config = await update_integration_config(db, self.config)

        self.config = type(self.config).model_validate(db_config)
        return db_config
    
    @is_saved
    async def get_ban(self, db: AsyncSession, response: schemas.Response) -> models.PlayerBan | None:
        """Get a player ban.

        Parameters
        ----------
        db : AsyncSession
            An asynchronous database session
        response : schemas.Response
            A player report response

        Returns
        -------
        models.PlayerBan | None
            This integration's ban associated with the report, if any
        """
        return await get_ban_by_player_and_integration(db,
            player_id=response.player_report.player_id,
            integration_id=self.config.id,
        )

    @is_saved
    async def set_ban_id(self, db: AsyncSession, response: schemas.Response, ban_id: str) -> models.PlayerBan:
        """Create a ban record

        Parameters
        ----------
        db : AsyncSession
            An asynchronous database session
        response : schemas.Response
            A player report response
        ban_id : str
            The ban identifier

        Returns
        -------
        models.PlayerBan
            The ban record

        Raises
        ------
        AlreadyBannedError
            The player is already banned
        """
        ban = schemas.PlayerBanCreateParams(
            player_id=response.player_report.player_id,
            integration_id=self.config.id,
            remote_id=ban_id,
        )
        try:
            db_ban = await create_ban(db, ban)
        except Exception as e:
            raise AlreadyBannedError(response, str(e))
        return db_ban
    
    @is_saved
    async def set_multiple_ban_ids(self, db: AsyncSession, responses_banids: Sequence[tuple[schemas.Response, str]]):
        """Create multiple ban records.

        In case a player is already banned and a conflict
        arises, it is silently ignored.

        Parameters
        ----------
        db : AsyncSession
            An asynchronous database session
        responses_banids : Sequence[tuple[schemas.Response, str]]
            A sequence of player report responses with their
            associated ban identifier.
        """
        bans = [
            schemas.PlayerBan(
                player_id=response.player_report.player_id,
                integration_id=self.config.id,
                remote_id=ban_id,
            ).model_dump()
            for response, ban_id in responses_banids
        ]
        await bulk_create_bans(db, bans)
    
    @is_saved
    async def discard_ban_id(self, db: AsyncSession, response: schemas.Response):
        """Delete a ban record

        Parameters
        ----------
        db : AsyncSession
            An asynchronous database session
        response : schemas.Response
            A player report response

        Raises
        ------
        NotFoundError
            No ban record could be found
        """
        db_ban = await self.get_ban(db, response)
        if not db_ban:
            raise NotFoundError("Ban does not exist")
        await db.delete(db_ban)
        await db.commit()

    @is_saved
    async def discard_multiple_ban_ids(self, db: AsyncSession, responses: Sequence[schemas.Response]):
        """Deletes all ban records that are associated
        with any of the given responses

        Parameters
        ----------
        db : AsyncSession
            An asynchronous database session
        responses : Sequence[schemas.Response]
            A sequence of player report responses
        """
        await bulk_delete_bans(db,
            models.PlayerBan.player_id.in_([response.player_report.player_id for response in responses]),
            models.PlayerBan.integration_id==self.config.id,
        )
    
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
        IntegrationValidationError
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
        IntegrationBanError
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
        NotFoundError
            The player is not known to be banned.
        IntegrationBanError
            Failed to unban the player.
        """
        raise NotImplementedError

    @abstractmethod
    async def bulk_ban_players(self, responses: Sequence[schemas.Response]):
        """Instruct the remote integration to ban multiple players.
        Depending on the implementation this may take a while.

        Players that are already banned will be silently ignored, but
        should optimally be left out to avoid unnecessary requests.

        Parameters
        ----------
        response : Sequence[schemas.Response]
            The community's responses to all rapported players

        Raises
        ------
        IntegrationBulkBanError
            Failed to ban one or more players.
        """
        raise NotImplementedError

    @abstractmethod
    async def bulk_unban_players(self, responses: Sequence[schemas.Response]):
        """Instruct the remote integration to unban multiple players.
        Depending on the implementation this may take a while.

        Players that are not banned will be silently ignored, but should
        optimally be left out to avoid unnecessary requests.

        Parameters
        ----------
        response : Sequence[schemas.Response]
            The community's responses to all rapported players

        Raises
        ------
        IntegrationBulkBanError
            Failed to unban one or more players.
        """
        raise NotImplementedError
