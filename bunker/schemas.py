from datetime import datetime, timedelta, timezone
from pydantic import BaseModel, Field
from typing import Optional, ClassVar
from uuid import UUID

from bunker.db import models
from bunker.enums import ReportRejectReason, IntegrationType


class IntegrationConfigBase(BaseModel):
    id: int | None

    community_id: int
    integration_type: IntegrationType
    enabled: bool = True

    api_key: str
    api_url: str

class IntegrationConfig(IntegrationConfigBase):
    id: int

    class Config:
        from_attributes = True

class BattlemetricsIntegrationConfigParams(IntegrationConfigBase):
    id: int | None = None
    integration_type: ClassVar[IntegrationType] = IntegrationType.BATTLEMETRICS
    api_url: str = "https://api.battlemetrics.com"

    organization_id: str
    banlist_id: Optional[UUID] = None

class BattlemetricsIntegrationConfig(IntegrationConfig, BattlemetricsIntegrationConfigParams):
    pass

class CRCONIntegrationConfigParams(IntegrationConfigBase):
    id: int | None = None
    integration_type: ClassVar[IntegrationType] = IntegrationType.COMMUNITY_RCON

    bunker_api_key_id: int

class CRCONIntegrationConfig(IntegrationConfig, CRCONIntegrationConfigParams):
    pass


class AdminBase(BaseModel):
    discord_id: int
    community_id: Optional[int]
    name: str

class AdminCreateParams(AdminBase):
    pass

class Admin(AdminBase):
    class Config:
        from_attributes = True


class CommunityBase(BaseModel):
    name: str
    contact_url: str
    owner_id: int

    forward_guild_id: Optional[int]
    forward_channel_id: Optional[int]

class CommunityCreateParams(CommunityBase):
    owner_name: str

class Community(CommunityBase):
    id: int
    owner: Admin
    admins: list[Admin]
    
    class Config:
        from_attributes = True

class CommunityWithIntegrations(Community):
    integrations: list[BattlemetricsIntegrationConfig | CRCONIntegrationConfig]


class TokenBase(BaseModel):
    community_id: int
    admin_id: int
    expires_at: datetime

class TokenCreateParams(TokenBase):
    expires_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc) + timedelta(days=1))

class Token(TokenBase):
    id: int
    token: str

    class Config:
        from_attributes = True

class TokenWithRelations(Token):
    community: Community
    admin: Admin


class ReportBase(BaseModel):
    timestamp: datetime
    body: str

class PlayerCreateParams(BaseModel):
    id: str
    bm_rcon_url: Optional[str]

class ReportPlayerCreateParams(PlayerCreateParams):
    name: str

class ReportCreateParams(ReportBase):
    token: TokenWithRelations
    reasons: list[str]
    players: list[ReportPlayerCreateParams]
    attachment_urls: list[str] = Field(default_factory=list)

class ReportReason(BaseModel):
    report_id: int
    reason: str

    class Config:
        from_attributes = True

class Player(BaseModel):
    id: str
    bm_rcon_url: str | None

class PlayerReport(BaseModel):
    id: int
    player_id: str
    report_id: int
    player_name: str
    player: Player

    class Config:
        from_attributes = True

class ReportAttachment(BaseModel):
    report_id: int
    url: str

    class Config:
        from_attributes = True

class Report(ReportBase):
    id: int
    message_id: int
    token: Token
    reasons: list[ReportReason]
    players: list[PlayerReport]
    attachments: list[ReportAttachment]

    class Config:
        from_attributes = True

class ReportSubmissionPlayerData(ReportPlayerCreateParams):
    bm_rcon_url: Optional[str] = Field(alias="bmRconUrl")

class ReportSubmissionData(BaseModel):
    token: str
    players: list[ReportSubmissionPlayerData]
    reasons: list[str]
    description: str
    attachments: list[str]

class ReportSubmission(BaseModel):
    id: int
    timestamp: datetime
    data: ReportSubmissionData

class PlayerReport(BaseModel):
    id: int
    player_id: str
    report_id: int
    player_name: str

    class Config:
        from_attributes = True


class ResponseReport(ReportBase):
    id: int
    message_id: int
    token: Token
    reasons: list[ReportReason]
    attachments: list[ReportAttachment]

    class Config:
        from_attributes = True

class ResponsePlayer(PlayerReport):
    report: ResponseReport


class ResponseBase(BaseModel):
    banned: bool
    reject_reason: Optional[ReportRejectReason] = None

class ResponseCreateParams(ResponseBase):
    pr_id: int
    community_id: int

class PendingResponse(ResponseBase):
    banned: Optional[bool] = None

    player_report: ResponsePlayer
    community: CommunityWithIntegrations

class Response(PendingResponse):
    id: int
    pr_id: int
    banned: bool

    class Config:
        from_attributes = True


class PlayerBan(BaseModel):
    prr_id: int
    integration_id: int
    remote_id: str

    class Config:
        from_attributes = True
