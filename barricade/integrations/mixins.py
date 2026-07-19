import itertools
from abc import ABC, abstractmethod

from barricade import schemas
from barricade.crud.bans import bulk_delete_bans
from barricade.db import models, session_factory
from barricade.enums import Game
from barricade.exceptions import (
    IntegrationMissingPermissionsError,
    IntegrationValidationError,
)
from barricade.integrations.integration import Integration
from barricade.integrations.scope import Scope
from barricade.utils import game_switch


class IntegrationScopedMixin(Integration, ABC):
    @abstractmethod
    def get_required_scopes(self) -> set[Scope]:
        """Return the set of scopes that this integration requires."""
        raise NotImplementedError

    @abstractmethod
    def get_optional_scopes(self) -> set[Scope]:
        """Return the set of scopes that this integration optionally supports."""
        raise NotImplementedError

    @abstractmethod
    async def get_scopes(self) -> set[Scope]:
        """Fetch the set of scopes that this integration has access to."""
        raise NotImplementedError

    async def validate_scopes(self) -> set[str]:
        try:
            scopes = await self.get_scopes()
        except Exception as e:
            raise IntegrationValidationError("Failed to retrieve API scopes") from e

        params = self.config.model_dump()
        required_scopes = self.get_required_scopes()
        optional_scopes = self.get_optional_scopes()
        missing_scopes = {s for s in itertools.chain(required_scopes, optional_scopes)}
        for scope in scopes:
            for expected_scope in list(missing_scopes):
                if scope.covers(expected_scope, params=params):
                    missing_scopes.remove(expected_scope)

        missing_required_scopes = missing_scopes & required_scopes
        missing_optional_scopes = missing_scopes & optional_scopes

        if missing_required_scopes:
            raise IntegrationMissingPermissionsError(
                {str(s) for s in missing_required_scopes},
                "Missing scopes: {}".format(
                    ", ".join([str(s) for s in missing_scopes])
                ),
            )

        return {str(s) for s in missing_optional_scopes}


class IntegrationBanListMixin(Integration, ABC):
    @abstractmethod
    async def create_remote_ban_list(self, community: schemas.Community, game: Game):
        """Create a ban list for the given game."""
        raise NotImplementedError

    @abstractmethod
    async def validate_remote_ban_list(self, banlist_id: str) -> None:
        """Validate that the ban list exists and is accessible.

        Parameters
        ----------
        banlist_id : str
            The ID of the ban list to validate.

        Raises
        ------
        IntegrationValidationError
            If the ban list could not be validated due to forseen circumstances.
            Signals that the ban list should be recreated.
        Exception
            If validating the ban list failed due to unexpected circumstances.
        """
        raise NotImplementedError

    async def create_ban_list(self, community: schemas.Community, game: Game) -> str:
        self.logger.info("%r: Creating new ban list for game %s", self, game)

        banlist_id = game_switch(
            game, self.config.hll_banlist_id, self.config.hllv_banlist_id
        )

        if banlist_id:
            self.logger.info(
                "%s: Clearing bans from previous ban list %s", self, banlist_id
            )
            async with session_factory.begin() as db:
                await bulk_delete_bans(
                    db, models.PlayerBan.integration_id == self.config.id
                )

        try:
            new_banlist_id = await self.create_remote_ban_list(community, game)
        except Exception:
            self.logger.exception(
                "%r: Failed to create ban list for game %s", self, game
            )
            raise

        if game == Game.HLL:
            self.config.hll_banlist_id = new_banlist_id
        elif game == Game.HLLV:
            self.config.hllv_banlist_id = new_banlist_id
        else:
            raise ValueError(f"Unrecognized game: {game}")

        async with session_factory.begin() as db:
            await self.update(db)

        return new_banlist_id

    async def validate_ban_lists(self, community: schemas.Community):
        for game in Game:
            await self._validate_ban_list(community, game)

    async def _validate_ban_list(self, community: schemas.Community, game: Game):
        banlist_id = game_switch(
            game, self.config.hll_banlist_id, self.config.hllv_banlist_id
        )

        # First create a new ban list if there is no existing ban list.
        if not banlist_id:
            self.logger.warning(
                "%r: Failed to validate ban list for game %s: No banlist ID configured. Creating new...",
                self,
                game,
            )

            try:
                banlist_id = await self.create_ban_list(community, game)
            except Exception as e:
                raise IntegrationValidationError(
                    f"Failed to create ban list for game {game}"
                ) from e

        try:
            # Validate the individual ban list
            await self.validate_remote_ban_list(banlist_id)
        except IntegrationValidationError:
            # Recreate the ban list if validation failed for expected reasons
            self.logger.error(
                "%r: Ban list #%s failed validation! Creating new list.",
                self,
                banlist_id,
            )
            banlist_id = await self.create_ban_list(community, game)
            await self.validate_remote_ban_list(banlist_id)
        except Exception as e:
            # Raise an error if validation failed for unexpected reasons
            raise IntegrationValidationError(
                f"Unexpected error while validating ban list {banlist_id}"
            ) from e
