from barricade.db import ModelBase

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .player_report import PlayerReport
    from .player_ban import PlayerBan

class Player(ModelBase):
    __tablename__ = "players"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    bm_rcon_url: Mapped[Optional[str]]

    reports: Mapped[list['PlayerReport']] = relationship(back_populates="player")
    bans: Mapped[list['PlayerBan']] = relationship(back_populates="player")
