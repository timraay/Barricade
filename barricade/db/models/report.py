from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    ARRAY,
    TIMESTAMP,
    BigInteger,
    Enum,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from barricade.db import ModelBase
from barricade.enums import Game

if TYPE_CHECKING:
    from .player_report import PlayerReport
    from .report_message import ReportMessage
    from .report_token import ReportToken


class Report(ModelBase):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(
        ForeignKey("report_tokens.id", ondelete="CASCADE"), primary_key=True
    )
    message_id: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(True), server_default=func.now()
    )
    edited_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(True))
    edited_by: Mapped[str | None] = mapped_column(String)
    reasons_bitflag: Mapped[int] = mapped_column(Integer)
    reasons_custom: Mapped[str | None]
    body: Mapped[str]
    attachment_urls: Mapped[list[str]] = mapped_column(ARRAY(String))
    game: Mapped[Game] = mapped_column(Enum(Game), server_default=Game.HLL.name)
    platforms_bitflag: Mapped[int] = mapped_column(Integer)

    token: Mapped["ReportToken"] = relationship(
        back_populates="report", cascade="all, delete"
    )
    players: Mapped[list["PlayerReport"]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )
    messages: Mapped[list["ReportMessage"]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )
