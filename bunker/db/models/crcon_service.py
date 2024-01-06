from bunker.db import ModelBase

from sqlalchemy import ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .community import Community

class CRCONService(ModelBase):
    __tablename__ = "crcon_services"

    community_id: Mapped[int] = mapped_column(ForeignKey("communities.id"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="1")

    api_key: Mapped[str]
    api_url: Mapped[str]

    community: Mapped['Community'] = relationship(back_populates="crcon_service")
