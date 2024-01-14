from bunker.db import ModelBase
from bunker.enums import ServiceType

from sqlalchemy import Integer, Boolean, ForeignKey, Enum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from uuid import UUID

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .community import Community
    from .player_ban import PlayerBan
    from .web_token import WebToken

class Service(ModelBase):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    community_id: Mapped[int] = mapped_column(ForeignKey("communities.id"))
    service_type: Mapped[ServiceType] = mapped_column(Enum(ServiceType))
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="1")

    api_key: Mapped[str]
    api_url: Mapped[str]

    # Battlemetrics
    organization_id: Mapped[Optional[str]]
    banlist_id: Mapped[Optional[UUID]]

    # Community RCON
    bunker_api_key_id: Mapped[Optional[int]] = mapped_column(ForeignKey("web_tokens.id"), nullable=True)

    community: Mapped['Community'] = relationship(back_populates="services")
    bans: Mapped[list['PlayerBan']] = relationship(back_populates="service")
    bunker_api_key: Mapped[Optional['WebToken']] = relationship(back_populates="services")
