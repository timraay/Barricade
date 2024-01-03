from datetime import datetime, timedelta, timezone
import discord
from pydantic import BaseModel, Field
from typing import Optional, ClassVar
from uuid import UUID

from bunker.db import models

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

class PlayerReport(BaseModel):
    id: int
    player_id: str
    report_id: int
    player_name: str

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

class ResponseCreateParams(ResponseBase):
    pr_id: int
    community_id: int

class PendingResponse(ResponseBase):
    banned: Optional[bool] = None

    player_report: ResponsePlayer
    community: Community

class Response(PendingResponse):
    pr_id: int
    banned: bool

    class Config:
        from_attributes = True


class ServiceConfig(BaseModel):
    name: ClassVar[str]
    emoji: ClassVar[str]
    api_key: str
    enabled: bool = True

    @classmethod
    def create(cls, community: models.Community) -> 'ServiceConfig' | None:
        return None
    
    def get_url(self) -> str:
        return ""

class BattlemetricsServiceConfig(ServiceConfig):
    name: ClassVar[str] = "Battlemetrics"
    emoji: ClassVar[str] = "🤕"
    organization_id: str
    banlist_id: Optional[UUID] = None

    @classmethod
    def create(cls, community: models.Community):
        if community.battlemetrics_service:
            return cls.model_validate(community.battlemetrics_service)
    
    def get_url(self) -> str:
        return f"https://battlemetrics.com/rcon/orgs/edit/{self.organization_id}"

class CRCONServiceConfig(ServiceConfig):
    name: ClassVar[str] = "Community RCON"
    emoji: ClassVar[str] = "🤩"
    api_url: str

    @classmethod
    def create(cls, community: models.Community):
        if community.crcon_service:
            return cls.model_validate(community.crcon_service)
    
    def get_url(self) -> str:
        return self.api_url.removesuffix("api/")


class DiscordMessagePayload(BaseModel):
    class Config:
        arbitrary_types_allowed = True
    content: Optional[str] = None
    embeds: Optional[list[discord.Embed]] = None
