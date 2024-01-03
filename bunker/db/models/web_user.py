from bunker.db import ModelBase

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

class WebUser(ModelBase):
    __tablename__ = "web_users"

    username: Mapped[str] = mapped_column(String, primary_key=True)
    hashed_password: Mapped[str]
    scopes: Mapped[int] = mapped_column(Integer, default=0)
