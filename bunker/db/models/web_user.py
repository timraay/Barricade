from bunker.db import ModelBase

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .web_token import WebToken

class WebUser(ModelBase):
    __tablename__ = "web_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True)
    hashed_password: Mapped[str]
    scopes: Mapped[int] = mapped_column(Integer, default=0)

    tokens: Mapped[list['WebToken']] = relationship(back_populates="user")
