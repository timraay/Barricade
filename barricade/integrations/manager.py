from typing import TYPE_CHECKING, Optional

from barricade import schemas
from barricade.utils import Singleton, safe_create_task

if TYPE_CHECKING:
    from barricade.integrations import Integration


class IntegrationManager(Singleton):
    __integrations: dict[int, "Integration"] = {}

    def get_by_id(self, integration_id: int) -> Optional["Integration"]:
        integration = self.__integrations.get(integration_id)
        return integration

    def get_by_config(
        self, config: schemas.IntegrationConfigParams
    ) -> Optional["Integration"]:
        # Make sure config has an ID
        if config.id is None:
            raise ValueError("Config must have an ID")

        integration = self.get_by_id(config.id)
        if integration:
            integration.replace_config(config)
        return integration

    def get_all(self):
        yield from self.__integrations.values()

    def add(self, integration: "Integration"):
        if not integration.config.id:
            raise TypeError("Integration must be saved first")

        # Check if already exists
        if integration.config.id in self.__integrations:
            raise ValueError(
                f"An integration with ID {integration.config.id} already exists"
            )

        self.__integrations[integration.config.id] = integration
        if integration.config.enabled:
            safe_create_task(integration.enable(force=True))

        integration.logger.info("Added %r to manager", integration)

    def remove(self, integration_id: int):
        integration = self.__integrations.pop(integration_id, None)
        if not integration:
            raise ValueError(f"No integration found with ID {integration_id}")

        if integration.config.enabled:
            safe_create_task(integration.disable())

        integration.logger.info("Removed %r from manager", integration)
