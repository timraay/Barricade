from typing import Annotated

from fastapi import FastAPI, APIRouter, Depends, Security, HTTPException, status, Query

from sqlalchemy import select

from bunker.db import DatabaseDep, models
from bunker.web import schemas
from bunker.web.scopes import Scopes
from bunker.web.security import (
    get_active_token,
    get_user_by_username,
    get_active_token_of_user,
    verify_password,
    get_password_hash,
)

router = APIRouter(prefix="/users", tags=["Web Users"])

@router.get("", response_model=list[schemas.WebUser])
async def get_all_web_users(
        token: Annotated[schemas.TokenWithHash, Security(get_active_token, scopes=Scopes.STAFF.to_list())],
        db: DatabaseDep
):
    stmt = select(models.WebUser)
    result = await db.scalars(stmt)
    return result.all()

@router.post("", response_model=schemas.WebUser)
async def create_new_web_user(
    user: schemas.WebUserCreateParams,
        token: Annotated[schemas.TokenWithHash, Security(get_active_token, scopes=Scopes.STAFF.to_list())],
        db: DatabaseDep
):
    db_user = models.WebUser(
        **user.model_dump(exclude={"password"}),
        hashed_password=get_password_hash(user.password),
    )
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user

@router.delete("")
async def delete_web_user(
        user: schemas.WebUserDelete,
        token: Annotated[schemas.TokenWithHash, Security(get_active_token, scopes=Scopes.STAFF.to_list())],
        db: DatabaseDep
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


@router.get("/me", response_model=schemas.WebUser)
async def read_current_user(
        token: Annotated[schemas.TokenWithHash, Depends(get_active_token_of_user)]
):
    return token.user

@router.put("/me/password")
async def update_current_user_password(
        old_password: str,
        new_password: Annotated[str, Query(min_length=8, max_length=64)],
        token: Annotated[models.WebToken, Depends(get_active_token_of_user)],
        db: DatabaseDep
):
    if not verify_password(old_password, token.user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect password"
        )
    
    token.user.hashed_password = get_password_hash(new_password)
    await db.commit()
    return True


def setup(app: FastAPI):
    app.include_router(router)
