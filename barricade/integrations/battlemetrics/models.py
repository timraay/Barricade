from enum import Enum
from typing import Optional
from uuid import uuid1
from pydantic import UUID1, BaseModel, Field

class ServerRequestType(Enum):
    ack = "ack"
    error = "error"
    SERVER_UPDATE = "SERVER_UPDATE"
    ACTIVITY = "ACTIVITY"
    RESOURCE_UPDATED = "RESOURCE_UPDATED"

class ClientRequestType(Enum):
    auth = "auth"
    filter = "filter"
    join = "join"
    ping = "ping"

class Packet(BaseModel):
    i: UUID1 = Field(default_factory=uuid1)
    t: ServerRequestType | ClientRequestType
    p: Optional[dict | list | str] = None
    c: Optional[str] = None

    def is_response(self):
        return self.t == ServerRequestType.ack or self.t == ServerRequestType.error
    
