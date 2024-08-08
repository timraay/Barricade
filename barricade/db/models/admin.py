from barricade.db import ModelBase

from sqlalchemy import BigInteger, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .community import Community
    from .report_token import ReportToken

class Admin(ModelBase):
    __tablename__ = "admins"

    discord_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str]
    community_id: Mapped[int] = mapped_column(ForeignKey("communities.id"), nullable=True)

    community: Mapped['Community'] = relationship(back_populates="admins", foreign_keys=[community_id])
    owned_community: Mapped[Optional['Community']] = relationship(back_populates="owner", foreign_keys="Community.owner_id")
    tokens: Mapped[list['ReportToken']] = relationship(back_populates="admin")
