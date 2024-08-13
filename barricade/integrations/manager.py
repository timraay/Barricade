from barricade import schemas
from barricade.utils import Singleton

from typing import TYPE_CHECKING, Optional
if TYPE_CHECKING:
    from barricade.integrations import Integration

class IntegrationManager(Singleton):
    __integrations: dict[int, 'Integration'] = {}

    def get_by_id(self, integration_id: int) -> Optional['Integration']:
        integration = self.__integrations.get(integration_id)
        return integration
    
    def get_by_config(self, config: schemas.IntegrationConfig) -> Optional['Integration']:
        # Make sure config has an ID
        if config.id is None:
            raise ValueError("Config must have an ID")
        
        integration = self.get_by_id(config.id)
        if integration:
            integration.config = integration.meta.config_cls.model_validate(config)
        return integration
    
    def add(self, integration: 'Integration'):
        if not integration.config.id:
            raise TypeError("Integration must be saved first")
        
        # Check if already exists
        if integration.config.id in self.__integrations:
            raise ValueError("An integration with ID %s already exists" % integration.config.id)
        
        self.__integrations[integration.config.id] = integration
    
    def remove(self, integration_id: int):
        integration = self.__integrations.pop(integration_id, None)
        if not integration:
            raise ValueError("No integration found with ID %s" % integration_id)
    