from fastapi import FastAPI, APIRouter, HTTPException, status, Depends
from typing import Annotated

from bunker import schemas
from bunker.crud import communities
from bunker.exceptions import AlreadyExistsError, AdminNotAssociatedError
from bunker.db import models, DatabaseDep
from bunker.web.paginator import PaginatorDep, PaginatedResponse

router = APIRouter(prefix="/communities")

def get_community_dependency(load_relations: bool):
    async def inner(db: DatabaseDep, community_id: int):
        result = await communities.get_community_by_id(db, community_id, load_relations=load_relations)
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Community does not exist"
            )
        return result
    return inner
CommunityDep = Annotated[models.Community, Depends(get_community_dependency(False))]
CommunityWithRelationsDep = Annotated[models.Community, Depends(get_community_dependency(True))]

def get_admin_dependency(load_relations: bool):
    async def inner(db: DatabaseDep, admin_id: int):
        result = await communities.get_admin_by_id(db, admin_id, load_relations=load_relations)
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Admin does not exist"
            )
        return result
    return inner
AdminDep = Annotated[models.Admin, Depends(get_admin_dependency(False))]
AdminWithRelationsDep = Annotated[models.Admin, Depends(get_admin_dependency(True))]


@router.get("", response_model=PaginatedResponse[schemas.Community])
async def get_all_communities(
        db: DatabaseDep,
        paginator: PaginatorDep,
):
    result = await communities.get_all_communities(db,
        limit=paginator.limit,
        offset=paginator.offset,
    )
    return paginator.paginate(result)

@router.post("", response_model=schemas.CommunityRef)
async def create_community(
        db: DatabaseDep,
        community: schemas.CommunityCreateParams,
):
    # Create the community
    try:
        db_community = await communities.create_new_community(db, community)
    except AlreadyExistsError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admin is already owner of a community. Transfer ownership first."
        )

    return db_community

@router.get("/{community_id}", response_model=schemas.CommunityWithRelations)
async def get_community(
        community: CommunityWithRelationsDep,
):
    return community

@router.put("/{community_id}/owner")
async def transfer_community_ownership(
        db: DatabaseDep,
        community: CommunityDep,
        admin: AdminDep,
) -> bool:
    try:
        return await communities.transfer_ownership(db, community, admin)
    except AdminNotAssociatedError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admin is not part of the community"
        )

def setup(app: FastAPI):
    app.include_router(router)
