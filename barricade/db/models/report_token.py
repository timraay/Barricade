import secrets
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import TIMESTAMP, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from barricade.constants import REPORT_TOKEN_EXPIRE_DELTA
from barricade.db import ModelBase
from barricade.enums import Platform

if TYPE_CHECKING:
    from .admin import Admin
    from .community import Community
    from .report import Report


class ReportToken(ModelBase):
    __tablename__ = "report_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    value: Mapped[str] = mapped_column(
        String, unique=True, index=True, default=lambda: ReportToken.generate_value()
    )
    community_id: Mapped[int] = mapped_column(ForeignKey("communities.id"))
    admin_id: Mapped[int] = mapped_column(ForeignKey("admins.discord_id"))
    expires_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(True), server_default=(func.now() + REPORT_TOKEN_EXPIRE_DELTA)
    )  # type: ignore
    platform: Mapped[Platform] = mapped_column(
        Enum(Platform), default=Platform.PC, server_default=Platform.PC.name
    )

    community: Mapped["Community"] = relationship(
        back_populates="tokens", lazy="selectin"
    )
    admin: Mapped["Admin"] = relationship(back_populates="tokens", lazy="selectin")
    report: Mapped[Optional["Report"]] = relationship(
        back_populates="token", cascade="all, delete"
    )

    def is_expired(self):
        return datetime.now(tz=UTC) >= self.expires_at

    @staticmethod
    def generate_value():
        return secrets.token_urlsafe(16)
