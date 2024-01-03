from abc import ABC, abstractmethod

from bunker import schemas

class Service(ABC):
    def __init__(self, display_name: str, config: schemas.ServiceConfig):
        self.display_name = display_name
        self.config = config

    @abstractmethod
    async def validate(self, response: schemas.Response):
        pass

    @abstractmethod
    async def confirm_report(self, response: schemas.Response):
        pass
