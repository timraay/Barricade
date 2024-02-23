from datetime import datetime, timedelta, timezone
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, ClassVar
from uuid import UUID

from bunker.enums import ReportRejectReason, IntegrationType, ReportReasonFlag

class _ModelFromAttributes(BaseModel):
    model_config=ConfigDict(from_attributes=True)

class IntegrationConfigParams(_ModelFromAttributes):
    id: int | None

    community_id: int
    integration_type: IntegrationType
    enabled: bool = True

    api_key: str
    api_url: str
    
    organization_id: Optional[str]
    banlist_id: Optional[UUID]
    bunker_api_key_id: Optional[int]

class BattlemetricsIntegrationConfigParams(IntegrationConfigParams):
    id: int | None = None
    api_url: str = "https://api.battlemetrics.com"

    integration_type: ClassVar[IntegrationType] = IntegrationType.BATTLEMETRICS
    bunker_api_key_id: ClassVar[Optional[int]] = None

class CRCONIntegrationConfigParams(IntegrationConfigParams):
    id: int | None = None

    integration_type: ClassVar[IntegrationType] = IntegrationType.COMMUNITY_RCON
    organization_id: ClassVar[Optional[str]] = None
    banlist_id: ClassVar[Optional[UUID]] = None

class IntegrationConfig(IntegrationConfigParams):
    id: int

class BattlemetricsIntegrationConfig(BattlemetricsIntegrationConfigParams, IntegrationConfig):
    pass

class CRCONIntegrationConfig(CRCONIntegrationConfigParams, IntegrationConfig):
    pass


class _AdminBase(BaseModel):
    discord_id: int
    community_id: Optional[int]
    name: str

class _CommunityBase(BaseModel):
    name: str
    contact_url: str
    owner_id: int

    forward_guild_id: Optional[int]
    forward_channel_id: Optional[int]

class _PlayerBase(BaseModel):
    id: str
    bm_rcon_url: Optional[str]

class _ReportTokenBase(BaseModel):
    community_id: int
    admin_id: int
    expires_at: datetime

class _ReportBase(BaseModel):
    created_at: datetime
    body: str
    reasons_bitflag: ReportReasonFlag
    reasons_custom: Optional[str]
    attachment_urls: list[str]

class _PlayerReportBase(BaseModel):
    player_id: str
    player_name: str

class _ResponseBase(BaseModel):
    pr_id: int
    community_id: int
    banned: bool
    reject_reason: Optional[ReportRejectReason]

class _PlayerBanBase(BaseModel):
    player_id: int
    integration_id: int
    remote_id: str



class AdminRef(_AdminBase, _ModelFromAttributes):
    pass

class CommunityRef(_CommunityBase, _ModelFromAttributes):
    id: int

class PlayerRef(_PlayerBase, _ModelFromAttributes):
    pass

class ReportTokenRef(_ReportTokenBase, _ModelFromAttributes):
    id: int
    value: str

    community: CommunityRef
    admin: AdminRef

class ReportRef(_ReportBase, _ModelFromAttributes):
    id: int
    message_id: int

class PlayerBanRef(_PlayerBanBase, _ModelFromAttributes):
    id: int



class AdminCreateParams(_AdminBase):
    pass

class CommunityCreateParams(_CommunityBase):
    owner_name: str

class Admin(AdminRef):
    community: Optional[CommunityRef]

class Community(CommunityRef):
    owner: AdminRef
    admins: list[AdminRef]
    integrations: list[IntegrationConfig]


class ReportTokenCreateParams(_ReportTokenBase):
    expires_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc) + timedelta(days=1))

class ReportToken(ReportTokenRef):
    report: Optional[ReportRef]


class ReportAttachment(_ModelFromAttributes):
    report_id: int
    url: str

class PlayerCreateParams(_PlayerBase):
    pass

class PlayerReportCreateParams(_PlayerReportBase):
    bm_rcon_url: Optional[str]

class PlayerReport(_PlayerReportBase, _ModelFromAttributes):
    id: int
    report_id: int

    player: PlayerRef
    report: ReportRef

class ReportCreateParams(_ReportBase):
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    token: ReportTokenRef
    players: list[PlayerReportCreateParams] = Field(min_length=1)
    attachment_urls: list[str] = Field(default_factory=list)

class Report(ReportRef):
    players: list[PlayerReport]
    attachment_urls: list[ReportAttachment]

class ReportWithToken(Report):
    token: ReportTokenRef

class ResponseCreateParams(_ResponseBase):
    reject_reason: Optional[ReportRejectReason] = None

class Response(_ResponseBase, _ModelFromAttributes):
    id: int
    player_report: PlayerReport
    community: CommunityRef

class PendingResponse(_ResponseBase):
    player_report: PlayerReport
    community: CommunityRef
    banned: Optional[bool] = None


class Player(PlayerRef):
    reports: list[PlayerReport]
    
class PlayerBan(PlayerBanRef):
    player: PlayerRef
    integration: IntegrationConfig

class PlayerBanCreateParams(_PlayerBanBase):
    pass

class ReportSubmissionPlayerData(PlayerReportCreateParams):
    bm_rcon_url: Optional[str] = Field(alias="bmRconUrl")

class ReportSubmissionData(BaseModel):
    token: str
    players: list[ReportSubmissionPlayerData]
    reasons: list[str]
    body: str
    attachment_urls: list[str] = Field(alias="attachmentUrls")

class ReportSubmission(BaseModel):
    id: int
    timestamp: datetime
    data: ReportSubmissionData

class ResponseStats(BaseModel):
    num_banned: int
    num_rejected: int
    reject_reasons: dict[ReportRejectReason, int]


class IntegrationBanPlayerParams(BaseModel):
    player_id: str
    reasons: list[str]
    community: CommunityRef
