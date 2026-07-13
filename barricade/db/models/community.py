from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from barricade.db import ModelBase

if TYPE_CHECKING:
    from .admin import Admin
    from .integration import Integration
    from .player_report_response import PlayerReportResponse
    from .player_watchlist import PlayerWatchlist
    from .report_message import ReportMessage
    from .report_token import ReportToken
    from .web_token import WebToken


class Community(ModelBase):
    __tablename__ = "communities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    tag: Mapped[str]
    contact_url: Mapped[str]
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("admins.discord_id"))

    games_bitflag: Mapped[int] = mapped_column(Integer)

    guild_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, unique=True, index=True
    )

    hll_reports_channel_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    hll_alerts_channel_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    hll_confirmations_channel_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    hll_admin_role_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    hll_alerts_role_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    hll_platform_filter: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hll_reason_filter: Mapped[int | None] = mapped_column(Integer, nullable=True)

    hllv_reports_channel_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    hllv_alerts_channel_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    hllv_confirmations_channel_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    hllv_admin_role_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    hllv_alerts_role_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    hllv_platform_filter: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hllv_reason_filter: Mapped[int | None] = mapped_column(Integer, nullable=True)

    admins: Mapped[list["Admin"]] = relationship(
        back_populates="community", foreign_keys="Admin.community_id"
    )
    owner: Mapped[Optional["Admin"]] = relationship(
        back_populates="owned_community", foreign_keys=[owner_id]
    )
    tokens: Mapped[list["ReportToken"]] = relationship(back_populates="community")
    messages: Mapped[list["ReportMessage"]] = relationship(back_populates="community")
    responses: Mapped[list["PlayerReportResponse"]] = relationship(
        back_populates="community"
    )
    watchlists: Mapped[list["PlayerWatchlist"]] = relationship(
        back_populates="community"
    )
    integrations: Mapped[list["Integration"]] = relationship(
        back_populates="community", order_by="Integration.id"
    )
    api_keys: Mapped[list["WebToken"]] = relationship(back_populates="community")

    def __repr__(self) -> str:
        return f'Community[id={self.id}, name="{self.name}"]'
