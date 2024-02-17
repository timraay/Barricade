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
    get_user_by_username,
    get_active_token_of_user,
    verify_password,
    get_password_hash,
    authenticate_user,
    create_token
)
from bunker.db import models, get_db

router = APIRouter(prefix="")


@router.post("/login", response_model=schemas.Login)
async def login_for_access_token(
        form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
        db: AsyncSession = Depends(get_db)
):
    user = await authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(status_code=400, detail="Incorrect username or password")
    
    _, access_token = await create_token(db=db, user=user)
    
    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/users", response_model=list[schemas.WebUser])
async def get_all_web_users(
        current_user: Annotated[schemas.WebUserBase, Security(get_active_token_of_user, scopes=Scopes.STAFF.to_list())],
        db: AsyncSession = Depends(get_db)
):
    stmt = select(models.WebUser)
    result = await db.scalars(stmt)
    return result.all()

@router.post("/users", response_model=schemas.WebUser)
async def create_new_web_user(
    user: schemas.WebUserCreateParams,
        current_user: Annotated[schemas.WebUserBase, Security(get_active_token_of_user, scopes=Scopes.STAFF.to_list())],
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

@router.delete("/users")
async def delete_web_user(
        user: schemas.WebUserDelete,
        current_user: Annotated[schemas.WebUserBase, Security(get_active_token_of_user, scopes=Scopes.STAFF.to_list())],
        db: AsyncSession = Depends(get_db)
):
    db_user = await get_user_by_username(db, user.username)
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
        current_user: Annotated[schemas.WebUser, Depends(get_active_token_of_user)]
):
    return current_user

@router.put("/users/me/password")
async def update_current_user_password(
        old_password: str,
        new_password: Annotated[str, Query(min_length=8, max_length=64)],
        current_user: Annotated[models.WebUser, Depends(get_active_token_of_user)],
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
