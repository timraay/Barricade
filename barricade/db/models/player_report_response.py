from barricade.db import ModelBase
from barricade.enums import ReportRejectReason

from sqlalchemy import Integer, ForeignKey, Enum, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .community import Community
    from .player_report import PlayerReport

class PlayerReportResponse(ModelBase):
    __tablename__ = "player_report_responses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pr_id: Mapped[int] = mapped_column(ForeignKey("player_reports.id", ondelete="CASCADE"))
    community_id: Mapped[int] = mapped_column(ForeignKey("communities.id"))
    banned: Mapped[bool]
    reject_reason: Mapped[Optional[ReportRejectReason]] = mapped_column(Enum(ReportRejectReason), nullable=True)

    player_report: Mapped['PlayerReport'] = relationship(back_populates="responses", lazy="selectin")
    community: Mapped['Community'] = relationship(back_populates="responses", lazy="selectin")

    __table_args__ = (
        UniqueConstraint('pr_id', 'community_id'),
    )
