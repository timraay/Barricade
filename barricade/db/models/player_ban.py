from barricade.db import ModelBase

from sqlalchemy import Integer, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .integration import Integration
    from .player import Player

class PlayerBan(ModelBase):
    __tablename__ = "player_bans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[str] = mapped_column(ForeignKey("players.id"))
    integration_id: Mapped[int] = mapped_column(ForeignKey("integrations.id", ondelete="CASCADE"))

    remote_id: Mapped[str]

    player: Mapped['Player'] = relationship(back_populates="bans")
    integration: Mapped['Integration'] = relationship(back_populates="bans")

    __table_args__ = (
        UniqueConstraint('player_id', 'integration_id'),
    )

