from datetime import datetime, timezone
import hashlib
import logging
from typing import Annotated
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import Depends, HTTPException, status
from fastapi.security import (
    OAuth2PasswordBearer,
    SecurityScopes,
)
from passlib.context import CryptContext

from barricade.crud.communities import get_community_by_id
from barricade.db import DatabaseDep, models
from barricade.web import schemas as web_schemas
from barricade.web.scopes import Scopes

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def generate_token_value():
    return str(uuid.uuid4())

async def create_user(db: AsyncSession, user: web_schemas.WebUserCreateParams) -> models.WebUser:
    db_user = models.WebUser(
        **user.model_dump(exclude={"password"}),
        hashed_password=get_password_hash(user.password),
    )
    db.add(db_user)
    await db.flush()
    await db.refresh(db_user)
    return db_user

async def create_token(
        db: AsyncSession,
        token: web_schemas.TokenCreateParams,
) -> tuple[models.WebToken, str]:
    if token.scopes is None and token.user_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="\"scopes\" and \"user\" cannot both be null"
        )
    
    if token.expires_delta is None:
        expires = None
    else:
        if token.expires_delta.total_seconds() < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="\"expires_delta\" must not be negative"
            )
        expires = datetime.now(tz=timezone.utc) + token.expires_delta

    token_value = generate_token_value()
    hashed_token_value = get_token_hash(token_value)
    db_token = models.WebToken(
        hashed_token=hashed_token_value,
        scopes=token.scopes,
        expires=expires,
        user_id=token.user_id,
        community_id=token.community_id
    )
    db.add(db_token)
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either user_id or community_id is an unknown identifier"
        )
    return db_token, token_value


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


def verify_token(plain_token: str, hashed_token: str) -> bool:
    return get_token_hash(plain_token) == hashed_token

def get_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()



async def get_user_by_username(db: DatabaseDep, username: str):
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
    stmt = select(models.WebUser).where(models.WebUser.username == username).limit(1)
    db_user = await db.scalar(stmt)
    return db_user

async def get_token_by_value(db: AsyncSession, token_value: str):
    hashed_token_value = get_token_hash(token_value)
    stmt = select(models.WebToken).where(models.WebToken.hashed_token == hashed_token_value).limit(1)
    db_token = await db.scalar(stmt)
    return db_token

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
    user = await get_user_by_username(db, username)
    if not user:
        return False
    if not verify_password(password, user.hashed_password):
        return False
    return user


async def get_active_token(
        security_scopes: SecurityScopes,
        token: Annotated[str, Depends(oauth2_scheme)],
        db: DatabaseDep,
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

    db_token = await get_token_by_value(db, token)
    if not db_token:
        raise credentials_exception
    
    if db_token.expires and db_token.expires < datetime.now(tz=timezone.utc):
        await db.delete(db_token)
        await db.flush()
        raise credentials_exception

    if db_token.scopes is not None:
        permitted_scopes = Scopes(db_token.scopes)
    elif db_token.user is not None:
        permitted_scopes = Scopes(db_token.user.scopes)
    else:
        logging.warn("No scopes found on token with ID %r", db_token.id)
        permitted_scopes = Scopes(0)

    required_scopes = Scopes.from_list(security_scopes.scopes)
    missing_scopes = required_scopes ^ (required_scopes & permitted_scopes)

    if missing_scopes:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not enough permissions",
            headers={"WWW-Authenticate": authenticate_value},
        )

    return db_token

async def get_active_token_of_user(
        token: Annotated[web_schemas.TokenWithHash, Depends(get_active_token)],
):
    if not token.user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token not associated with user",
        )

    return token


async def get_active_token_of_community(
        token: Annotated[web_schemas.TokenWithHash, Depends(get_active_token)],
):
    if token.community_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token not associated with community",
        )

    return token

def get_active_token_community(load_relations: bool):
    async def inner(
        token: Annotated[web_schemas.TokenWithHash, Depends(get_active_token_of_community)],
        db: DatabaseDep,
    ):
        assert token.community_id is not None
        result = await get_community_by_id(
            db,
            community_id=token.community_id,
            load_relations=load_relations
        )
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Community does not exist"
            )
        return result
    return inner
