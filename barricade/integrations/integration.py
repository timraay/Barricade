from abc import ABC, abstractmethod
import asyncio
from functools import wraps
import logging
from pydantic import BaseModel
import random
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Sequence

from barricade import schemas
from barricade.crud.bans import get_ban_by_player_and_integration, create_ban, bulk_create_bans, bulk_delete_bans
from barricade.crud.communities import get_community_by_id
from barricade.crud.integrations import create_integration_config, update_integration_config
from barricade.db import session_factory
from barricade.discord.communities import safe_send_to_community
from barricade.discord.utils import get_danger_embed
from barricade.enums import IntegrationType
from barricade.exceptions import IntegrationValidationError, NotFoundError, AlreadyBannedError
from barricade.db import models
from barricade.integrations.manager import IntegrationManager
from barricade.utils import safe_create_task

manager = IntegrationManager()

def is_saved(func):
    @wraps(func)
    async def decorator(integration: 'Integration', *args, **kwargs):
        if integration.config.id is None:
            raise RuntimeError("Integration needs to be created first")
        return await func(integration, *args, **kwargs)
    return decorator

class IntegrationMetaData(BaseModel):
    name: str
    config_cls: type[schemas.IntegrationConfig]
    type: IntegrationType
    emoji: str

class Integration(ABC):
    # TODO: Check if defined in subclasses using __init_subclass__?
    meta: IntegrationMetaData

    def __init__(self, config: schemas.IntegrationConfigParams):
        self.config = config
        self.task: asyncio.Task | None = None
        self.lock = asyncio.Lock()

    def __repr__(self):
        return f"{type(self).__name__}[id={self.config.id}]"
    
    # --- Integration state

    async def create(self):
        if self.config.id is not None:
            raise RuntimeError("Integration was already created")
        
        async with session_factory.begin() as db:
            self.config.enabled = False
            db_config = await create_integration_config(db, self.config) # type: ignore
            self.config = db_config
            manager.add(self)
    
    @is_saved
    async def update(self, db: AsyncSession):
        db_config = await update_integration_config(db, self.config) # type: ignore
        self.config = db_config
        return db_config

    @is_saved
    async def enable(self) -> models.Integration:
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
        if self.config.enabled is True:
            raise RuntimeError("Integration is already enabled")

        try:
            self.config.enabled = True
            async with session_factory.begin() as db:
                db_config = await self.update(db)
                self.start_connection()

                if not self.task or self.task.done():
                    self.task = safe_create_task(self._loop())
                
                return db_config
        except:
            # Reset state
            self.config.enabled = False
            self.stop_connection()

            if self.task and not self.task.done():
                self.task.cancel()

            raise

    @is_saved
    async def disable(self) -> models.Integration:
        """Disable this integration.

        Updates and saves the config.

        Returns
        -------
        models.Integration
            The integration config record
        """
        if self.config.enabled is False:
            raise RuntimeError("Integration is already disabled")
        
        try:
            self.config.enabled = False
            async with session_factory.begin() as db:
                db_config = await self.update(db)
                self.stop_connection()

                if self.task and not self.task.done():
                    self.task.cancel()
                self.task = None
                
                return db_config
        except:
            # Reset state
            self.config.enabled = True
            self.start_connection()
            
            if self.task and self.task.done():
                self.task = safe_create_task(self._loop())
            
            raise

    async def _loop(self):
        while True:
            # Sleep 12-24 hours
            await asyncio.sleep(60 * 60 * random.randrange(12, 24))

            if not self.config.enabled:
                logging.error("Wanted to synchronize %r but was unexpectedly disabled")
                return
            
            async with session_factory() as db:
                db_community = await get_community_by_id(db, self.config.community_id)
                community = schemas.Community.model_validate(db_community)
            
            try:
                await self.validate(community)
            except Exception as e:
                if isinstance(e, IntegrationValidationError):
                    description = f"-# During validation we ran into the following issue:\n-# `{e}`"
                else:
                    description = f"-# During validation we ran into an unexpected issue. Please reach out to Barricade staff if this keeps reoccuring."
                
                safe_send_to_community(community, embed=get_danger_embed(
                    f"Your {self.meta.name} integration was disabled!",
                    description
                ))
                # We kind of have to pray that this doesn't fail for whatever reason.
                # We can't await it, because we would cancel ourselves.
                safe_create_task(self.disable())
                return

            try:
                await self.synchronize()
            except:
                logging.exception("Failed to synchronize ban lists for %r", self)

    # --- Connection hooks

    def start_connection(self):
        pass

    def stop_connection(self):
        pass

    def update_connection(self):
        pass

    # --- Everything related to storing and retrieving bans

    @is_saved
    async def get_ban(self, db: AsyncSession, player_id: str) -> models.PlayerBan | None:
        """Get a player ban.

        Parameters
        ----------
        db : AsyncSession
            An asynchronous database session
        player_id : str
            The ID of a player

        Returns
        -------
        models.PlayerBan | None
            This integration's ban associated with the player, if any
        """
        return await get_ban_by_player_and_integration(db,
            player_id=player_id,
            integration_id=self.config.id, # type: ignore
        )

    @is_saved
    async def set_ban_id(self, db: AsyncSession, player_id: str, ban_id: str) -> models.PlayerBan:
        """Create a ban record

        Parameters
        ----------
        db : AsyncSession
            An asynchronous database session
        player_id : str
            The ID of a player
        ban_id : str
            The ID of the ban this player received

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
            player_id=player_id,
            integration_id=self.config.id, # type: ignore
            remote_id=ban_id,
        )
        try:
            db_ban = await create_ban(db, ban)
        except Exception as e:
            raise AlreadyBannedError(player_id, str(e))
        return db_ban
    
    @is_saved
    async def set_multiple_ban_ids(self, db: AsyncSession, *playerids_banids: tuple[str, str]):
        """Create multiple ban records.

        In case a player is already banned and a conflict
        arises, it is silently ignored.

        Parameters
        ----------
        db : AsyncSession
            An asynchronous database session
        playerids_banids : tuple[schemas.Response, str]
            A sequence of player IDs with their associated
            ban IDs.
        """
        bans = [
            schemas.PlayerBanCreateParams(
                player_id=player_id,
                integration_id=self.config.id, # type: ignore
                remote_id=ban_id,
            )
            for player_id, ban_id in playerids_banids
        ]
        await bulk_create_bans(db, bans)
    
    @is_saved
    async def discard_ban_id(self, db: AsyncSession, player_id: str):
        """Delete a ban record

        Parameters
        ----------
        db : AsyncSession
            An asynchronous database session
        player_id : str
            The ID of a player

        Raises
        ------
        NotFoundError
            No ban record could be found
        """
        db_ban = await self.get_ban(db, player_id)
        if not db_ban:
            raise NotFoundError("Ban does not exist")
        await db.delete(db_ban)
        await db.flush()

    @is_saved
    async def discard_multiple_ban_ids(self, db: AsyncSession, player_ids: Sequence[str]):
        """Deletes all ban records that are associated
        with any of the given responses

        Parameters
        ----------
        db : AsyncSession
            An asynchronous database session
        player_ids : Sequence[str]
            A sequence of player IDs
        """
        await bulk_delete_bans(db,
            models.PlayerBan.player_id.in_(player_ids),
            models.PlayerBan.integration_id==self.config.id,
        )
    
    def get_ban_reason(self, community: schemas.CommunityRef) -> str:
        return (
            "Banned via shared HLL Barricade report. Appeal"
            f" at {community.contact_url}"
        )

    # --- Commands to implement

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
    def get_instance_url(self) -> str:
        """Get a URL to the specific instance that this
        integration connects to.

        Returns
        -------
        str
            The URL of the connected instance."""

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
            The community's response to a reported player

        Raises
        ------
        IntegrationBanError
            Failed to ban the player.
        """
        raise NotImplementedError

    @abstractmethod
    async def unban_player(self, player_id: str):
        """Instruct the remote integration to unban a player, should
        they be banned.

        Parameters
        ----------
        response : schemas.Response
            The community's response to a reported player

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
            The community's responses to all reported players

        Raises
        ------
        IntegrationBulkBanError
            Failed to ban one or more players.
        """
        raise NotImplementedError

    @abstractmethod
    async def bulk_unban_players(self, player_ids: Sequence[str]):
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

    @abstractmethod
    async def synchronize(self):
        """Synchronize the local ban list with the remote ban list. If
        a ban exists either locally or remotely, but not both, remove it.

        Some integrations like Battlemetrics also track expired bans. In
        case a ban is expired, change the response.
        """
        raise NotImplementedError
