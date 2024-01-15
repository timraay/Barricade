from enum import StrEnum

class PlayerIDType(StrEnum):
    STEAM_64_ID = "steamID"
    UUID = "hllWindowsID"

class ReportRejectReason(StrEnum):
    INSUFFICIENT = "Insufficient"
    INCONCLUSIVE = "Inconclusive"

class IntegrationType(StrEnum):
    BATTLEMETRICS = "battlemetrics"
    COMMUNITY_RCON = "crcon"
