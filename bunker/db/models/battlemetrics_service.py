from bunker.db import ModelBase
from uuid import UUID

from sqlalchemy import ForeignKey, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .community import Community

class BattlemetricsService(ModelBase):
    __tablename__ = "battlemetrics_services"

    community_id: Mapped[int] = mapped_column(ForeignKey("communities.id"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=True)

    api_key: Mapped[str]
    organization_id: Mapped[str]
    banlist_id: Mapped[Optional[UUID]]

    community: Mapped['Community'] = relationship(back_populates="battlemetrics_service")
