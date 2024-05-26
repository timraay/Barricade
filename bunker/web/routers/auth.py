from typing import Annotated

from fastapi import FastAPI, APIRouter, Depends, HTTPException, Security
from fastapi.security import OAuth2PasswordRequestForm

from bunker.db import DatabaseDep
from bunker.web import schemas
from bunker.web.scopes import Scopes
from bunker.web.security import (
    get_active_token,
    authenticate_user,
    create_token
)

router = APIRouter(prefix="/auth", tags=["Authentication"])

@router.get("", response_model=schemas.BaseToken)
async def get_login_status(
        db_token: Annotated[schemas.TokenWithHash, Depends(get_active_token)],
):
    token = schemas.TokenWithHash.model_validate(db_token)
    if token.user:
        token.scopes = token.user.scopes
    return token

@router.post("/login", response_model=schemas.Login)
async def login_for_access_token(
        form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
        db: DatabaseDep
):
    user = await authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(status_code=400, detail="Incorrect username or password")
    
    _, access_token = await create_token(db, schemas.TokenCreateParams(user_id=user.id))
    
    return {"access_token": access_token, "token_type": "bearer"}

@router.post("/token", response_model=schemas.Token)
async def create_access_token(
        token_data: schemas.TokenCreateParams,
        token: Annotated[schemas.TokenWithHash, Security(get_active_token, scopes=Scopes.STAFF.to_list())],
        db: DatabaseDep,
):
    db_token, token_value = await create_token(db=db, token=token_data)
    token = schemas.TokenWithHash.model_validate(db_token).model_dump()
    token["token"] = token_value
    return token

def setup(app: FastAPI):
    app.include_router(router)
