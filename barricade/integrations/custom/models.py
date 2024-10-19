import pydantic
from datetime import datetime
from enum import Enum

class ClientRequestType(str, Enum):
    BAN_PLAYERS = "ban_players"
    UNBAN_PLAYERS = "unban_players"
    NEW_REPORT = "new_report"

class ServerRequestType(str, Enum):
    SCAN_PLAYERS = "scan_players"

class RequestBody(pydantic.BaseModel):
    id: int
    request: ClientRequestType | ServerRequestType
    payload: dict | None = None

    def response_ok(self, payload: dict | None = None):
        return ResponseBody(id=self.id, response=payload)

    def response_error(self, error: str):
        return ResponseBody(id=self.id, response={'error': error}, failed=True)

class ResponseBody(pydantic.BaseModel):
    id: int
    request: None = None
    response: dict | None
    failed: bool = False

class UnbanPlayersRequestConfigPayload(pydantic.BaseModel):
    banlist_id: str | None
class BanPlayersRequestConfigPayload(UnbanPlayersRequestConfigPayload):
    reason: str

class BanPlayersRequestPayload(pydantic.BaseModel):
    player_ids: dict[str, str | None]
    config: BanPlayersRequestConfigPayload

class ScanPlayersRequestPayload(pydantic.BaseModel):
    player_ids: list[str]

class UnbanPlayersRequestPayload(pydantic.BaseModel):
    # Even though in theory these can all be converted to ints, we should safely
    # filter out all invalid record IDs later.
    ban_ids: list[str]
    config: UnbanPlayersRequestConfigPayload

class NewReportRequestPayloadPlayer(pydantic.BaseModel):
    player_id: str
    player_name: str
    bm_rcon_url: str | None
class NewReportRequestPayload(pydantic.BaseModel):
    created_at: datetime
    body: str
    reasons: list[str]
    attachment_urls: list[str]
    players: list[NewReportRequestPayloadPlayer]
