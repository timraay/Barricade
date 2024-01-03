from datetime import datetime

from bunker.db import ModelBase

from sqlalchemy import BigInteger, ForeignKey, TIMESTAMP, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .player_report import PlayerReport
    from .report_attachment import ReportAttachment
    from .report_reason import ReportReason
    from .report_token import ReportToken

class Report(ModelBase):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(ForeignKey("report_tokens.id"), primary_key=True)
    message_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    timestamp: Mapped[datetime] = mapped_column(TIMESTAMP(True), server_default=func.now())
    body: Mapped[str]

    token: Mapped['ReportToken'] = relationship(back_populates="report", lazy="selectin")
    players: Mapped[list['PlayerReport']] = relationship(back_populates="report", lazy="selectin")
    reasons: Mapped[list['ReportReason']] = relationship(back_populates="report", lazy="selectin")
    attachments: Mapped[list['ReportAttachment']] = relationship(back_populates="report", lazy="selectin")
