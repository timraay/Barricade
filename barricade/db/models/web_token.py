from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import TIMESTAMP, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from barricade.db import ModelBase

if TYPE_CHECKING:
    from .community import Community
    from .web_user import WebUser


class WebToken(ModelBase):
    __tablename__ = "web_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hashed_token: Mapped[str] = mapped_column(String, unique=True)
    scopes: Mapped[int | None]
    expires: Mapped[datetime | None] = mapped_column(TIMESTAMP(True), nullable=True)

    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("web_users.id", ondelete="CASCADE"), nullable=True
    )
    community_id: Mapped[int | None] = mapped_column(
        ForeignKey("communities.id", ondelete="CASCADE"), nullable=True
    )

    user: Mapped[Optional["WebUser"]] = relationship(
        back_populates="tokens", lazy="selectin", cascade="all, delete"
    )
    community: Mapped[Optional["Community"]] = relationship(
        back_populates="api_keys", cascade="all, delete"
    )
