from barricade.db import ModelBase

from sqlalchemy import BigInteger, Enum, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from typing import TYPE_CHECKING, Optional

from barricade.enums import ReportMessageType

if TYPE_CHECKING:
    from .community import Community
    from .report import Report

class ReportMessage(ModelBase):
    __tablename__ = "report_messages"

    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"))
    community_id: Mapped[Optional[int]] = mapped_column(ForeignKey("communities.id", ondelete="CASCADE"))
    channel_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    message_type: Mapped[ReportMessageType] = mapped_column(Enum(ReportMessageType), server_default=ReportMessageType.REVIEW.name)

    report: Mapped['Report'] = relationship(back_populates="messages")
    community: Mapped['Community'] = relationship(back_populates="messages")

    __table_args__ = (
        UniqueConstraint('report_id', 'community_id'),
        Index('ix_report_messages_report_id_community_id', 'report_id', 'community_id'),
    )
