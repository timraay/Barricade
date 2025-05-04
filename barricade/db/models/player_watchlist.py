from barricade.db import ModelBase

from sqlalchemy import Integer, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .community import Community
    from .player import Player

class PlayerWatchlist(ModelBase):
    __tablename__ = "player_watchlists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[str] = mapped_column(ForeignKey("players.id"))
    community_id: Mapped[int] = mapped_column(ForeignKey("communities.id", ondelete="CASCADE"))

    player: Mapped['Player'] = relationship(back_populates="watchlists")
    community: Mapped['Community'] = relationship(back_populates="watchlists")

    __table_args__ = (
        UniqueConstraint('player_id', 'community_id'),
    )
