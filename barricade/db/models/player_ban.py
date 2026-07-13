from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from barricade.db import ModelBase
from barricade.enums import Game

if TYPE_CHECKING:
    from .integration import Integration
    from .player import Player


class PlayerBan(ModelBase):
    __tablename__ = "player_bans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[str] = mapped_column(ForeignKey("players.id"))
    integration_id: Mapped[int] = mapped_column(
        ForeignKey("integrations.id", ondelete="CASCADE")
    )
    game: Mapped[Game] = mapped_column(Enum(Game), server_default=Game.HLL.name)

    remote_id: Mapped[str]

    player: Mapped["Player"] = relationship(back_populates="bans")
    integration: Mapped["Integration"] = relationship(back_populates="bans")

    __table_args__ = (UniqueConstraint("player_id", "integration_id"),)
