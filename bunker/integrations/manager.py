from sqlalchemy import select
from bunker import schemas
from bunker.db import models, session_factory
from bunker.enums import IntegrationType
from bunker.integrations import Integration
from bunker.integrations.battlemetrics import BattlemetricsIntegration
from bunker.integrations.crcon import CRCONIntegration
from bunker.utils import Singleton

def type_to_integration(integration_type: IntegrationType) -> type[Integration]:
    return next((
        integration for integration in IntegrationManager.types
        if integration.meta.type == integration_type
    ), None)

class IntegrationManager(Singleton):
    types = (
        BattlemetricsIntegration,
        CRCONIntegration,
    )

    __integrations: dict[int, Integration] = {}

    def get(self, integration_id: int):
        return self.__integrations.get(integration_id)
    
    def load(self, config: schemas.IntegrationConfig):
        integration = self.get(config.id)
        if not integration:
            integration = self.create(config)
        
        self.update(integration, config)
        return integration

    def update(self, integration: Integration, config: schemas.IntegrationConfig):
        if not isinstance(config, schemas.IntegrationConfig):
            raise TypeError("Config must be a schema")

        # Only update if config was actually changed
        if integration.config != config:
            # Make sure integration type remains unchanged
            expected_type = integration.config.integration_type
            if expected_type != config.integration_type:
                raise ValueError("Expected integration type %s" % expected_type)

            # Update the config
            integration.config = config
            return True
        
        # The config was identical
        return False

    def create(self, config: schemas.IntegrationConfig):
        if not isinstance(config, schemas.IntegrationConfig):
            raise TypeError("Config must be a schema")
        
        # Make sure config has an ID
        if config.id is None:
            raise TypeError("Config must have an ID")
        
        # Check if already exists
        if config.id in self.__integrations:
            raise ValueError("An integration with ID %s already exists" % config.id)
        
        # Initialize integration
        integration_cls = type_to_integration(config.integration_type)
        integration = integration_cls(config)

        # Add integration to dict
        self.__integrations[config.id] = integration

        return integration

    async def load_all(self):
        async with session_factory() as db:
            stmt = select(models.Integration)
            manager = IntegrationManager()
            results = await db.stream_scalars(stmt)
            async for config in results:
                manager.load(config)
