from datetime import datetime
from pydantic import BaseModel, Field

from bunker.web.scopes import Scopes

class WebUserBase(BaseModel):
    username: str

class WebUserDelete(WebUserBase):
    pass

class WebUserCreateParams(WebUserBase):
    username: str = Field(min_length=3, max_length=20)
    password: str = Field(min_length=8, max_length=64)
    scopes: Scopes = Scopes(0)

class WebUser(WebUserBase):
    id: int
    scopes: Scopes

    class Config:
        from_attributes = True

class WebUserWithHash(WebUser):
    hashed_password: str

class WebUserWithPassword(WebUser):
    password: str


class BaseToken(BaseModel):
    scopes: Scopes | None
    expires: datetime | None

    user_id: int | None
    community_id: int | None

    user: WebUser | None

class TokenWithHash(BaseToken):
    id: int
    hashed_token: str

    class Config:
        from_attributes = True

class Token(BaseToken):
    token: str

class Login(BaseModel):
    access_token: str
    token_type: str
