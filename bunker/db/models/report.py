from datetime import datetime

from bunker.db import ModelBase

from sqlalchemy import Integer, BigInteger, String, ForeignKey, TIMESTAMP, ARRAY, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .player_report import PlayerReport
    from .report_token import ReportToken

class Report(ModelBase):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(ForeignKey("report_tokens.id", ondelete="CASCADE"), primary_key=True)
    message_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    created_at: Mapped[datetime] = mapped_column(TIMESTAMP(True), server_default=func.now())
    reasons_bitflag: Mapped[int] = mapped_column(Integer)
    reasons_custom: Mapped[Optional[str]]
    body: Mapped[str]
    attachment_urls: Mapped[list[str]] = mapped_column(ARRAY(String))

    token: Mapped['ReportToken'] = relationship(back_populates="report", cascade="all, delete")
    players: Mapped[list['PlayerReport']] = relationship(back_populates="report", cascade="all, delete-orphan")
