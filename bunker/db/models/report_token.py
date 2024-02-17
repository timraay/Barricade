from datetime import datetime, timezone, timedelta
from typing import Optional, TYPE_CHECKING

from sqlalchemy import Integer, String, TIMESTAMP, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bunker.db import ModelBase

if TYPE_CHECKING:
    from .community import Community
    from .admin import Admin
    from .report import Report

class ReportToken(ModelBase):
    __tablename__ = "report_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    value: Mapped[str] = mapped_column(String, unique=True, index=True)
    community_id: Mapped[int] = mapped_column(ForeignKey("communities.id"))
    admin_id: Mapped[int] = mapped_column(ForeignKey("admins.discord_id"))
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(True), server_default=(func.now() - timedelta(days=1)))

    community: Mapped['Community'] = relationship(back_populates="tokens", lazy="selectin")
    admin: Mapped['Admin'] = relationship(back_populates="tokens", lazy="selectin")
    report: Mapped[Optional['Report']] = relationship(back_populates="token")

    def is_expired(self):
        return datetime.now(tz=timezone.utc) >= self.expires_at
