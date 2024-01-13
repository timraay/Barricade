from bunker.db import ModelBase

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .service import Service
    from .player_report_response import PlayerReportResponse

class PlayerBan(ModelBase):
    __tablename__ = "player_bans"

    prr_id: Mapped[int] = mapped_column(ForeignKey("player_report_responses.id"), primary_key=True)
    service_id: Mapped[int] = mapped_column(ForeignKey("services.id"), primary_key=True)

    remote_id: Mapped[str]

    response: Mapped['PlayerReportResponse'] = relationship(back_populates="bans")
    service: Mapped['Service'] = relationship(back_populates="bans")
