from bunker.db import ModelBase

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .community import Community
    from .player_report import PlayerReport

class PlayerReportResponse(ModelBase):
    __tablename__ = "player_report_responses"

    pr_id: Mapped[int] = mapped_column(ForeignKey("player_reports.id"), primary_key=True)
    community_id: Mapped[int] = mapped_column(ForeignKey("communities.id"), primary_key=True)
    banned: Mapped[bool]

    player_report: Mapped['PlayerReport'] = relationship(back_populates="responses", lazy="selectin")
    community: Mapped['Community'] = relationship(back_populates="responses", lazy="selectin")
