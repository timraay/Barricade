from barricade.db import ModelBase
from barricade.enums import IntegrationType

from sqlalchemy import Integer, Boolean, ForeignKey, Enum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from uuid import UUID

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .community import Community
    from .player_ban import PlayerBan
    from .web_token import WebToken

class Integration(ModelBase):
    __tablename__ = "integrations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    community_id: Mapped[int] = mapped_column(ForeignKey("communities.id"))
    integration_type: Mapped[IntegrationType] = mapped_column(Enum(IntegrationType))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="1")

    api_key: Mapped[str]
    api_url: Mapped[str]
    banlist_id: Mapped[Optional[str]]

    # Battlemetrics
    organization_id: Mapped[Optional[str]]

    community: Mapped['Community'] = relationship(back_populates="integrations")
    bans: Mapped[list['PlayerBan']] = relationship(back_populates="integration")
