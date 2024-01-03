from datetime import timedelta
from typing import Annotated

from fastapi import FastAPI, APIRouter, Depends, Security, HTTPException, status, Query
from fastapi.security import OAuth2PasswordRequestForm

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bunker.db import models
from bunker.web import schemas
from bunker.web.scopes import Scopes
from bunker.web.security import (
    get_current_user,
    verify_password,
    get_password_hash,
    authenticate_user,
    ACCESS_TOKEN_EXPIRE_MINUTES,
    create_access_token
)
from bunker.db import models, get_db

router = APIRouter(prefix="")


@router.post("/login", response_model=schemas.Token)
async def login_for_access_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: AsyncSession = Depends(get_db)
):
    user = await authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(status_code=400, detail="Incorrect username or password")
    
    requested_scopes = Scopes.from_list(form_data.scopes)
    blocked_scopes = requested_scopes ^ (requested_scopes & user.scopes)
    
    if blocked_scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Scopes not allowed: " + ", ".join(blocked_scopes.to_list())
        )

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        user.username,
        requested_scopes,
        expires_delta=access_token_expires,
    )

    return {"access_token": access_token, "token_type": "bearer", "scopes": requested_scopes}


@router.get("/users", response_model=list[schemas.WebUser])
async def get_all_web_users(
    current_user: Annotated[schemas.WebUserBase, Security(get_current_user, scopes=Scopes.STAFF.to_list())],
    db: AsyncSession = Depends(get_db)
):
    stmt = select(models.WebUser)
    result = await db.scalars(stmt)
    return result.all()

@router.post("/users", response_model=schemas.WebUser)
async def create_new_web_user(
    user: schemas.WebUserCreate,
    current_user: Annotated[schemas.WebUserBase, Security(get_current_user, scopes=Scopes.STAFF.to_list())],
    db: AsyncSession = Depends(get_db)
):
    db_user = models.WebUser(
        **user.model_dump(exclude={"password"}),
        hashed_password=get_password_hash(user.password),
    )
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user

@router.delete("/users", response_model=schemas.WebUser)
async def delete_web_user(
    user: schemas.WebUserDelete,
    current_user: Annotated[schemas.WebUserBase, Security(get_current_user, scopes=Scopes.STAFF.to_list())],
    db: AsyncSession = Depends(get_db)
):
    db_user = await db.get(models.WebUser, user.username)
    if db_user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Web user does not exist"
        )
    await db.delete(db_user)
    await db.commit()
    return True


@router.get("/users/me", response_model=schemas.WebUserBase)
async def read_current_user(
    current_user: Annotated[schemas.WebUser, Depends(get_current_user)]
):
    return current_user

@router.put("/users/me/password")
async def update_current_user_password(
    old_password: str,
    new_password: Annotated[str, Query(min_length=8, max_length=64)],
    current_user: Annotated[models.WebUser, Depends(get_current_user)],
    db: AsyncSession = Depends(get_db)
):
    if not verify_password(old_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect password"
        )
    
    current_user.hashed_password = get_password_hash(new_password)
    await db.commit()
    return True


def setup(app: FastAPI):
    app.include_router(router)
