from barricade.db import ModelBase

from sqlalchemy import BigInteger, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .community import Community
    from .report import Report

class ReportMessage(ModelBase):
    __tablename__ = "report_messages"

    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id", ondelete="CASCADE"), primary_key=True)
    community_id: Mapped[int] = mapped_column(ForeignKey("communities.id", ondelete="CASCADE"), primary_key=True)
    channel_id: Mapped[int] = mapped_column(BigInteger)
    message_id: Mapped[int] = mapped_column(BigInteger, index=True, unique=True)

    report: Mapped['Report'] = relationship(back_populates="messages")
    community: Mapped['Community'] = relationship(back_populates="messages")
