from bunker.db import ModelBase
from datetime import datetime

from sqlalchemy import Integer, String, TIMESTAMP, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .community import Community
    from .web_user import WebUser

class WebToken(ModelBase):
    __tablename__ = "web_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hashed_token: Mapped[str] = mapped_column(String, unique=True)
    scopes: Mapped[Optional[int]]
    expires: Mapped[Optional[datetime]] = mapped_column(TIMESTAMP(True), nullable=True)

    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("web_users.id", ondelete="CASCADE"), nullable=True)
    community_id: Mapped[Optional[int]] = mapped_column(ForeignKey("communities.id", ondelete="CASCADE"), nullable=True)

    user: Mapped[Optional['WebUser']] = relationship(back_populates="tokens", lazy="selectin", cascade="all, delete")
    community: Mapped[Optional['Community']] = relationship(back_populates="api_keys", cascade="all, delete")
