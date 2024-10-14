from abc import ABC, abstractmethod
import asyncio
from functools import wraps
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
from barricade.exceptions import AlreadyExistsError, IntegrationDisabledError, IntegrationValidationError, NotFoundError, AlreadyBannedError
from barricade.db import models
from barricade.integrations.manager import IntegrationManager
from barricade.logger import get_logger
from barricade.utils import safe_create_task

manager = IntegrationManager()

def is_saved(func):
    @wraps(func)
    async def decorator(integration: 'Integration', *args, **kwargs):
        if integration.config.id is None:
            raise RuntimeError("Integration needs to be created first")
        return await func(integration, *args, **kwargs)
    return decorator

def is_enabled(func):
    @wraps(func)
    async def decorator(integration: 'Integration', *args, **kwargs):
        if not integration.config.enabled:
            raise IntegrationDisabledError("Integration %r is disabled. Enable before retrying." % integration)
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
        if config.id is not None:
            existing = IntegrationManager().get_by_id(config.id)
            if existing:
                config = self.meta.config_cls.model_validate({
                    **existing.config.model_dump(),
                    **config.model_dump(exclude_unset=True)
                })
        self.config = config

        self.task: asyncio.Task | None = None
        self.lock = asyncio.Lock()
        self.logger = get_logger(self.config.community_id)

    def __repr__(self):
        return f"{type(self).__name__}[id={self.config.id}]"
    
    # --- Integration state

    async def create(self):
        if self.config.id is not None:
            raise RuntimeError("Integration was already created")
        
        async with session_factory.begin() as db:
            db_config = await create_integration_config(db, self.config) # type: ignore
            self.config = schemas.IntegrationConfig.model_validate(db_config)
            manager.add(self)
    
    @is_saved
    async def update(self, db: AsyncSession):
        db_config = await update_integration_config(db, self.config) # type: ignore
        self.config = self.meta.config_cls.model_validate(db_config)

        # Update connection
        self.update_connection()

        # Also update integration known to manager (if any)
        manager.get_by_config(self.config)
        
        return db_config

    @is_saved
    async def enable(self, force: bool = False) -> models.Integration:
        """Enable this integration.

        Updates and saves the config.

        Parameters
        ----------
        force : bool
            Whether to enable the integration if it already is.
            Throws a RuntimeError otherwise. False by default.

        Returns
        -------
        models.Integration
            The integration config record
        """
        if self.config.enabled is True and not force:
            raise RuntimeError("Integration is already enabled")

        try:
            self.config.enabled = True
            async with session_factory.begin() as db:
                db_config = await self.update(db)
                self.start_connection()

                if not self.task or self.task.done():
                    self.task = safe_create_task(self._loop(), name=f"IntegrationLoop{self.config.id}")
                
            self.logger.info("Enabled integration %r", self)    
            return db_config
        except:
            # Reset state
            self.config.enabled = False
            self.stop_connection()

            if self.task and not self.task.done():
                self.task.cancel()

            raise

    @is_saved
    async def disable(self, force: bool = False) -> models.Integration:
        """Disable this integration.

        Updates and saves the config.

        Parameters
        ----------
        force : bool
            Whether to disable the integration if it already is.
            Throws a RuntimeError otherwise. False by default.

        Returns
        -------
        models.Integration
            The integration config record
        """
        if self.config.enabled is False and not force:
            raise RuntimeError("Integration is already disabled")
        
        try:
            self.config.enabled = False
            async with session_factory.begin() as db:
                db_config = await self.update(db)
                self.stop_connection()

                if self.task and not self.task.done():
                    self.task.cancel()
                self.task = None

            self.logger.info("Disabled integration %r", self)    
            return db_config
        except:
            # Reset state
            self.config.enabled = True
            self.start_connection()
            
            if self.task and self.task.done():
                self.task = safe_create_task(self._loop(), name=f"IntegrationLoop{self.config.id}")
            
            raise

    async def _loop(self):
        self.logger.info("Starting loop for integration %r", self)    
        while True:
            # Sleep 12-24 hours
            await asyncio.sleep(60 * 60 * random.randrange(12, 24))

            if not self.config.enabled:
                self.logger.error("Wanted to synchronize %r but was unexpectedly disabled")
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
                safe_create_task(
                    self.disable(),
                    err_msg=f"Exited loop for integration {self!r} but failed to disable the integration!",
                )
                return

            try:
                await self.synchronize()
            except:
                self.logger.exception("Failed to synchronize ban lists for %r", self)

    # --- Connection hooks

    def start_connection(self):
        pass

    def stop_connection(self):
        pass

    def update_connection(self):
        pass

    async def on_report_create(self, report: schemas.ReportWithToken):
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
        self.logger.info("%r: Setting ban ID %s for player %s", self, ban_id, player_id)
        ban = schemas.PlayerBanCreateParams(
            player_id=player_id,
            integration_id=self.config.id, # type: ignore
            remote_id=ban_id,
        )
        try:
            db_ban = await create_ban(db, ban)
        except AlreadyExistsError as e:
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
        playerids_banids : tuple[str, str]
            A sequence of player IDs with their associated
            ban IDs.
        """
        self.logger.info("%r: Setting ban IDs in bulk: %s", self, playerids_banids)
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
        self.logger.info("%r: Discarding ban for player %s", self, player_id)
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
        self.logger.info("%r: Discarding bans in bulk: %s", self, ", ".join(player_ids))
        await bulk_delete_bans(db,
            models.PlayerBan.player_id.in_(player_ids),
            models.PlayerBan.integration_id==self.config.id,
        )
    
    def get_ban_reason(self, response: schemas.ResponseWithToken) -> str:
        report = response.player_report.report
        reporting_community = report.token.community
        return (
            f"Banned via shared HLL Barricade report for {', '.join(report.reasons_bitflag.to_list(report.reasons_custom))}."
            "\n\n"
            f"Reported by {reporting_community.name}\n"
            f"Contact: {reporting_community.contact_url}"
            "\n\n"
            f"Banned by {response.community.name}\n"
            f"Contact: {response.community.contact_url}"
            "\n\n"
            "More info: https://bit.ly/BarricadeBanned"
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
    async def validate(self, community: schemas.Community) -> set[str]:
        """Validate the integration's config.

        Parameters
        ----------
        community : schemas.Community
            The community owning this integration

        Returns
        -------
        set[str]
            A set of optional permissions that are missing

        Raises
        ------
        IntegrationValidationError
            A config value is incorrect or outdated
        """
        raise NotImplementedError

    @abstractmethod
    async def ban_player(self, response: schemas.ResponseWithToken):
        """Instruct the remote integration to ban a player.

        Parameters
        ----------
        response : schemas.ResponseWithToken
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
        player_id : str
            The ID of the player to unban

        Raises
        ------
        NotFoundError
            The player is not known to be banned.
        IntegrationBanError
            Failed to unban the player.
        """
        raise NotImplementedError

    @abstractmethod
    async def bulk_ban_players(self, responses: Sequence[schemas.ResponseWithToken]):
        """Instruct the remote integration to ban multiple players.
        Depending on the implementation this may take a while.

        Players that are already banned will be silently ignored, but
        should optimally be left out to avoid unnecessary requests.

        Parameters
        ----------
        response : Sequence[schemas.ResponseWithToken]
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
        response : Sequence[str]
            The IDs of the players to unban

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
