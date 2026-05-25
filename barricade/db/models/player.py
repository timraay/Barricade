from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from barricade.db import ModelBase

if TYPE_CHECKING:
    from .player_ban import PlayerBan
    from .player_report import PlayerReport
    from .player_watchlist import PlayerWatchlist


class Player(ModelBase):
    __tablename__ = "players"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    bm_rcon_url: Mapped[str | None]
    eos_id: Mapped[str | None]

    reports: Mapped[list["PlayerReport"]] = relationship(back_populates="player")
    bans: Mapped[list["PlayerBan"]] = relationship(back_populates="player")
    watchlists: Mapped[list["PlayerWatchlist"]] = relationship(back_populates="player")
