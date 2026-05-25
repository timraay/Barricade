from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship

from barricade.db import ModelBase

if TYPE_CHECKING:
    from .player import Player
    from .player_report_response import PlayerReportResponse
    from .report import Report


class PlayerReport(ModelBase):
    __tablename__ = "player_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[str] = mapped_column(ForeignKey("players.id"))
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id"))
    player_name: Mapped[str]

    report: Mapped["Report"] = relationship(back_populates="players", lazy="selectin")
    player: Mapped["Player"] = relationship(back_populates="reports", lazy="selectin")
    responses: Mapped[list["PlayerReportResponse"]] = relationship(
        back_populates="player_report", cascade="all, delete-orphan"
    )
