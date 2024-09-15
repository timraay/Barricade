from datetime import datetime, timezone
from pydantic import BaseModel, Field, ConfigDict, field_serializer, field_validator
from typing import Literal, Optional

from barricade.constants import REPORT_TOKEN_EXPIRE_DELTA
from barricade.enums import Platform, ReportRejectReason, IntegrationType, ReportReasonFlag

# Simple config to be used for ORM objects
class _ModelFromAttributes(BaseModel):
    model_config=ConfigDict(from_attributes=True)

# --- Integration configs

class SafeIntegrationConfigParams(_ModelFromAttributes):
    id: int | None

    community_id: int
    integration_type: IntegrationType
    enabled: bool = False

    api_url: str
    
    organization_id: Optional[str]
    banlist_id: Optional[str]

class IntegrationConfigParams(SafeIntegrationConfigParams):
    api_key: str

class BattlemetricsIntegrationConfigParams(IntegrationConfigParams):
    id: int | None = None
    api_url: str = "https://api.battlemetrics.com"

    integration_type: Literal[IntegrationType.BATTLEMETRICS] = IntegrationType.BATTLEMETRICS # type: ignore
    banlist_id: Optional[str] = None

class CustomIntegrationConfigParams(IntegrationConfigParams):
    id: int | None = None

    integration_type: Literal[IntegrationType.CUSTOM] = IntegrationType.CUSTOM # type: ignore
    organization_id: None = None # type: ignore
    banlist_id: Optional[str] = None

class CRCONIntegrationConfigParams(CustomIntegrationConfigParams):
    integration_type: Literal[IntegrationType.COMMUNITY_RCON] = IntegrationType.COMMUNITY_RCON # type: ignore

class SafeIntegrationConfig(SafeIntegrationConfigParams):
    id: int # type: ignore

    def __eq__(self, value: object) -> bool:
        # Existing __eq__ only works consistently if both are explicitly the same class
        if isinstance(value, type(self)):
            return self.model_dump() == value.model_dump()
        return super().__eq__(value)

class IntegrationConfig(SafeIntegrationConfig, IntegrationConfigParams): # type: ignore
    pass

class BattlemetricsIntegrationConfig(BattlemetricsIntegrationConfigParams, IntegrationConfig): # type: ignore
    pass

class CRCONIntegrationConfig(CRCONIntegrationConfigParams, IntegrationConfig): # type: ignore
    pass

class CustomIntegrationConfig(CustomIntegrationConfigParams, IntegrationConfig): # type: ignore
    pass


# --- Base classes
# These aren't directly used anywhere. They simply contain common
# attributes for further models created below.

class _AdminBase(BaseModel):
    discord_id: int
    community_id: Optional[int]
    name: str

    # Convert to str to avoid precision loss when sending via REST API
    @field_serializer('discord_id', when_used='json-unless-none')
    def convert_large_int_to_str(value: int): # type: ignore
        return str(value)

class _CommunityBase(BaseModel):
    name: str
    tag: str
    contact_url: str
    is_pc: bool
    is_console: bool

    forward_guild_id: Optional[int]
    forward_channel_id: Optional[int]
    admin_role_id: Optional[int]
    reasons_filter: Optional[ReportReasonFlag]

    @field_serializer(
            'forward_guild_id', 'forward_channel_id',
            when_used='json-unless-none'
    )
    def convert_large_int_to_str(value: int): # type: ignore
        return str(value)

class _PlayerBase(BaseModel):
    id: str
    bm_rcon_url: Optional[str]

class _ReportTokenBase(BaseModel):
    community_id: int
    admin_id: int
    expires_at: datetime
    platform: Platform

    @field_serializer('admin_id', when_used='json-unless-none')
    def convert_large_int_to_str(value: int): # type: ignore
        return str(value)

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
    responded_by: Optional[str]

class _PlayerBanBase(BaseModel):
    player_id: str
    integration_id: int
    remote_id: str

class _ReportMessageBase(BaseModel):
    report_id: int
    community_id: int
    channel_id: int
    message_id: int
    
    @field_serializer('message_id', when_used='json-unless-none')
    def convert_large_int_to_str(value: int): # type: ignore
        return str(value)


# --- Reference models
# These represent ORM entities with all of its primary keys and minimal relational attributes.
# Only if a relation is defined with a greedy loading strategy, for instance `lazy="selectin"`,
# it should be included here.

class AdminRef(_AdminBase, _ModelFromAttributes):
    def __repr__(self) -> str:
        return f"Admin[discord_id={self.discord_id}, community_id={self.community_id}, name=\"{self.name}\"]"

class CommunityRef(_CommunityBase, _ModelFromAttributes):
    id: int
    owner_id: int
    
    @field_serializer(
            'forward_guild_id', 'forward_channel_id', 'owner_id',
            when_used='json-unless-none'
    )
    def convert_large_int_to_str(value: int): # type: ignore
        return str(value)

    def __repr__(self) -> str:
        return f"Player[id={self.id}, name=\"{self.name}\"]"

class PlayerRef(_PlayerBase, _ModelFromAttributes):
    def __repr__(self) -> str:
        return f"Player[id={self.id}]"

class PlayerReportRef(_PlayerReportBase, _ModelFromAttributes):
    id: int
    report_id: int

    player: PlayerRef

    def __repr__(self) -> str:
        return f"PlayerReport[id={self.id}, report_id={self.report_id}, player_id=\"{self.player_id}\"]"

class SafeReportTokenRef(_ReportTokenBase, _ModelFromAttributes):
    id: int

    community: CommunityRef
    admin: AdminRef

class ReportTokenRef(SafeReportTokenRef):
    value: str

    def __repr__(self) -> str:
        return f"ReportToken[id={self.id}]"

class ReportRef(_ReportBase, _ModelFromAttributes):
    id: int
    message_id: int

    def __repr__(self) -> str:
        return f"Report[id={self.id}]"

class PlayerBanRef(_PlayerBanBase, _ModelFromAttributes):
    id: int

    def __repr__(self) -> str:
        return f"PlayerBan[id={self.id}, integration_id={self.integration_id}, player_id={self.player_id}]"

class ReportMessageRef(_ReportMessageBase, _ModelFromAttributes):
    def __repr__(self) -> str:
        return f"ReportMessage[community_id={self.community_id}, report_id={self.report_id}, message_id={self.message_id}]"


# --- ORM mapped models
# These classes directly wrap the results of CRUD methods. Any additional relations that are
# greedily loaded by these methods should be included here as well.

class Admin(AdminRef):
    community: Optional[CommunityRef]

class SafeCommunity(CommunityRef):
    owner: AdminRef
    admins: list[AdminRef]
    integrations: list[SafeIntegrationConfig]

class Community(SafeCommunity):
    integrations: list[IntegrationConfig] # type: ignore

class ReportTokenCreateParams(_ReportTokenBase):
    expires_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc) + REPORT_TOKEN_EXPIRE_DELTA)

class ReportToken(ReportTokenRef):
    report: Optional[ReportRef]

class ReportMessage(ReportMessageRef):
    report: ReportRef
    community: CommunityRef

class PlayerReport(PlayerReportRef):
    report: ReportRef

class ReportRefWithToken(ReportRef):
    token: ReportTokenRef
class PlayerReportWithToken(PlayerReport):
    report: ReportRefWithToken # type: ignore

class Report(ReportRef):
    players: list[PlayerReportRef]

class SafeReportWithToken(Report):
    token: SafeReportTokenRef
class ReportWithToken(SafeReportWithToken):
    token: ReportTokenRef # type: ignore

class ReportWithRelations(ReportWithToken):
    messages: list[ReportMessageRef]

class Response(_ResponseBase, _ModelFromAttributes):
    id: int
    player_report: PlayerReport
    community: CommunityRef

class ResponseWithToken(Response):
    player_report: PlayerReportWithToken # type: ignore

class SafeCommunityWithRelations(SafeCommunity):
    tokens: list[ReportTokenRef]
    responses: list[Response]

class CommunityWithRelations(Community, SafeCommunityWithRelations): # type: ignore
    pass

class Player(PlayerRef):
    reports: list[PlayerReport]
    
class PlayerBan(PlayerBanRef):
    player: PlayerRef
    integration: IntegrationConfig


# --- Entity creation parameters

class AdminCreateParams(_AdminBase):
    pass

class CommunityEditParams(_CommunityBase, _ModelFromAttributes):
    forward_guild_id: Optional[int]
    forward_channel_id: Optional[int]

    @field_validator('contact_url')
    @classmethod
    def strip_scheme_from_contact_url(cls, value: str):
        return value.removeprefix("https://").removesuffix("/")

class CommunityCreateParams(CommunityEditParams):
    owner_id: int
    owner_name: str
    forward_guild_id: Optional[int] = None
    forward_channel_id: Optional[int] = None
    admin_role_id: Optional[int] = None
    reasons_filter: Optional[ReportReasonFlag] = None

class PlayerCreateParams(_PlayerBase):
    pass

class PlayerReportCreateParams(_PlayerReportBase):
    bm_rcon_url: Optional[str]

class ReportEditParams(_ReportBase):
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    players: list[PlayerReportCreateParams] = Field(min_length=1)
    attachment_urls: list[str] = Field(default_factory=list)

class ReportCreateParams(ReportEditParams):
    token_id: int

class ReportCreateParamsTokenless(ReportEditParams):
    admin_id: int
    platform: Platform

    @field_serializer('admin_id', when_used='json-unless-none')
    def convert_large_int_to_str(value: int): # type: ignore
        return str(value)

class ReportMessageCreateParams(_ReportMessageBase):
    pass

class ResponseCreateParams(_ResponseBase):
    reject_reason: Optional[ReportRejectReason] = None

class PendingResponse(_ResponseBase):
    player_report: PlayerReportRef
    community: CommunityRef
    banned: Optional[bool] = None # type: ignore
    reject_reason: Optional[ReportRejectReason] = None
    responded_by: Optional[str] = None

class PlayerBanCreateParams(_PlayerBanBase):
    pass



# --- Report submission models

class ReportSubmissionPlayerData(PlayerReportCreateParams):
    model_config = ConfigDict(populate_by_name=True)
    player_id: str = Field(alias="playerId")
    player_name: str = Field(alias="playerName")
    bm_rcon_url: Optional[str] = Field(alias="bmRconUrl")

class ReportSubmissionData(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    token: str
    players: list[ReportSubmissionPlayerData]
    reasons: list[str]
    body: str
    attachment_urls: list[str] = Field(alias="attachmentUrls")

class ReportSubmission(BaseModel):
    id: str
    timestamp: datetime
    data: ReportSubmissionData

class ResponseStats(BaseModel):
    num_banned: int
    num_rejected: int
    reject_reasons: dict[ReportRejectReason, int]
