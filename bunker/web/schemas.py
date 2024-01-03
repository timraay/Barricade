from pydantic import BaseModel, Field

from bunker.web.scopes import Scopes

class Token(BaseModel):
    access_token: str
    token_type: str
    scopes: Scopes

class TokenData(BaseModel):
    username: str | None = None
    scopes: list[str] = []

class WebUserBase(BaseModel):
    username: str

class WebUserDelete(WebUserBase):
    pass

class WebUserCreate(WebUserBase):
    username: str = Field(min_length=3, max_length=20)
    password: str = Field(min_length=8, max_length=64)
    scopes: Scopes = Scopes(0)

class WebUser(WebUserBase):
    password: str
    scopes: Scopes

    class Config:
        from_attributes = True

class WebUserWithPassword(WebUser):
    hashed_password: str
