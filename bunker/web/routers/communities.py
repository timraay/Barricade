from fastapi import FastAPI, APIRouter, Depends, Security, HTTPException, status

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bunker import schemas
from bunker.communities import *
from bunker.db import models, get_db
from bunker.web.scopes import Scopes

router = APIRouter(prefix="/communities")

@router.get("", response_model=list[schemas.Community])
async def get_all_communities(
        db: AsyncSession = Depends(get_db)
):
    stmt = select(models.Community)
    result = await db.execute(stmt)
    return result.scalars().all()

@router.post("", response_model=schemas.Community)
async def create_community(
        community: schemas.CommunityCreateParams,
        db: AsyncSession = Depends(get_db)
):
    # Create the community
    try:
        db_community = await create_new_community(db, community)
    except AdminAlreadyAssociatedError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admin is already owner of a community. Transfer ownership first."
        )

    return db_community

# TODO: Join/leave endpoints

@router.get("/{community_id}", response_model=schemas.Community)
async def get_community(
        community_id: int,
        db: AsyncSession = Depends(get_db)
):
    db_community = await get_community_by_id(db, community_id, load_relations=True)
    if db_community is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Community does not exist"
        )
    return db_community

@router.post("/{community_id}/admins", response_model=schemas.Admin)
async def create_community_admin(
        admin: schemas.AdminCreateParams,
        db: AsyncSession = Depends(get_db)
):
    try:
        return await create_new_admin(db, admin)
    except AdminAlreadyAssociatedError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An admin with this ID already exists"
        )
    except NotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Community does not exist"
        )

@router.put("/{community_id}/owner")
async def transfer_community_ownership(
        community_id: int,
        admin_id: int,
        db: AsyncSession = Depends(get_db)
):
    db_community = await get_community_by_id(db, community_id)
    if db_community is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Community does not exist"
        )
    
    db_admin = await get_admin_by_id(db, admin_id)
    if db_admin is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Admin does not exist"
        )

    try:
        return await transfer_ownership(db, db_community, db_admin)
    except AdminNotAssociatedError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admin is not part of the community"
        )

def setup(app: FastAPI):
    app.include_router(router)
