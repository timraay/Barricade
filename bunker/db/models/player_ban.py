from bunker.db import ModelBase

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .integration import Integration
    from .player_report_response import PlayerReportResponse

class PlayerBan(ModelBase):
    __tablename__ = "player_bans"

    prr_id: Mapped[int] = mapped_column(ForeignKey("player_report_responses.id"), primary_key=True)
    integration_id: Mapped[int] = mapped_column(ForeignKey("integrations.id"), primary_key=True)

    remote_id: Mapped[str]

    response: Mapped['PlayerReportResponse'] = relationship(back_populates="bans")
    integration: Mapped['Integration'] = relationship(back_populates="bans")
