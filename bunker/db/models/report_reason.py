from bunker.db import ModelBase

from sqlalchemy import String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .report import Report

class ReportReason(ModelBase):
    __tablename__ = "report_reasons"

    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id"))
    reason: Mapped[str] = mapped_column(String, primary_key=True)

    report: Mapped['Report'] = relationship(back_populates="reasons")

