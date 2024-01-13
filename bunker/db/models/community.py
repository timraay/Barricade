from bunker.db import ModelBase

from sqlalchemy import Integer, BigInteger, String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .admin import Admin
    from .report_token import ReportToken
    from .player_report_response import PlayerReportResponse
    from .service import Service
    from .web_token import WebToken

class Community(ModelBase):
    __tablename__ = "communities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    contact_url: Mapped[str]
    owner_id: Mapped[int] = mapped_column(ForeignKey("admins.discord_id"))

    forward_guild_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    forward_channel_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    admins: Mapped[list['Admin']] = relationship(back_populates="community", foreign_keys="Admin.community_id", lazy="selectin")
    owner: Mapped['Admin'] = relationship(back_populates="owned_community", foreign_keys=[owner_id], lazy="selectin")
    tokens: Mapped[list['ReportToken']] = relationship(back_populates="community")
    responses: Mapped[list['PlayerReportResponse']] = relationship(back_populates="community")
    services: Mapped[list['Service']] = relationship(back_populates="community", lazy="selectin")
    api_keys: Mapped[list['WebToken']] = relationship(back_populates="community")
