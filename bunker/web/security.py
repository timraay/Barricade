from datetime import datetime, timedelta
from typing import Annotated

from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import Depends, HTTPException, status
from fastapi.security import (
    OAuth2PasswordBearer,
    SecurityScopes,
)
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import ValidationError

from bunker.db import models, get_db
from bunker.web.scopes import Scopes

# to get a string like this run:
# openssl rand -hex 32
SECRET_KEY = "09d25e094faa6ca2556c818166b7a9563b93f7099f6f0f4caa6cf63b88e8d3e7"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 24*60 # 1 day

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="login",
    scopes=Scopes.all().to_dict(),
)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify if the given plain password is the same
    as the given hashed password.

    Parameters
    ----------
    plain_password : str
        A plain password
    hashed_password : str
        A hashed password

    Returns
    -------
    bool
        Whether the two passwords are the same
    """
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    """Hash a given plain password

    Parameters
    ----------
    password : str
        A plain password

    Returns
    -------
    str
        The hashed equivalent of the given password
    """
    return pwd_context.hash(password)

async def get_user(db: AsyncSession, username: str):
    """Find a user by their username

    Parameters
    ----------
    db : AsyncSession
        An asynchronous database session
    username : str
        The user's name

    Returns
    -------
    WebUser | None
        The user, or None if they do not exist
    """
    return await db.get(models.WebUser, username)

async def authenticate_user(db: AsyncSession, username: str, password: str):
    """Authenthicate a user by their username and password

    Parameters
    ----------
    db : AsyncSession
        An asynchronous database session
    username : str
        The username
    password : str
        The password

    Returns
    -------
    WebUser | Literal[False]
        The authenticated user, or False if they could not be
        authenticated
    """
    user = await get_user(db, username)
    if not user:
        return False
    if not verify_password(password, user.hashed_password):
        return False
    return user

def create_access_token(username: str, scopes: Scopes, expires_delta: timedelta | None = None) -> str:
    """Create a new access token

    Parameters
    ----------
    username : str
        The name of the user the token belongs to
    scopes : Scopes
        The scopes that should be granted to the user
    expires_delta : timedelta | None, optional
        How long the token should last for before expiring, by
        default None

    Returns
    -------
    str
        A new access token
    """
    to_encode = {"sub": username, "scopes": scopes.to_list()}
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user(
    security_scopes: SecurityScopes,
    token: Annotated[str, Depends(oauth2_scheme)],
    db: AsyncSession = Depends(get_db),
):
    if security_scopes.scopes:
        authenticate_value = f'Bearer scope="{security_scopes.scope_str}"'
    else:
        authenticate_value = "Bearer"

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": authenticate_value},
    )

    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        requested_scopes = Scopes.from_list(payload.get("scopes", []))
    except (JWTError, ValidationError):
        raise credentials_exception

    user = await get_user(db, username)
    if user is None:
        raise credentials_exception

    required_scopes = Scopes.from_list(security_scopes.scopes)
    missing_scopes = required_scopes ^ (required_scopes & requested_scopes)

    if missing_scopes:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not enough permissions",
            headers={"WWW-Authenticate": authenticate_value},
        )

    return user
