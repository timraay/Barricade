from fastapi import FastAPI, APIRouter, HTTPException, Security, status, Depends
from typing import Annotated

from barricade import schemas
from barricade.crud import communities
from barricade.exceptions import AlreadyExistsError, AdminNotAssociatedError
from barricade.db import models, DatabaseDep
from barricade.web import schemas as web_schemas
from barricade.web.paginator import PaginatorDep, PaginatedResponse
from barricade.web.scopes import Scopes
from barricade.web.security import get_active_token, get_active_token_community

router = APIRouter(prefix="/communities", tags=["Communities"])

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


@router.get("", response_model=PaginatedResponse[schemas.SafeCommunity])
async def get_all_communities(
        db: DatabaseDep,
        paginator: PaginatorDep,
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token, scopes=Scopes.COMMUNITY_READ.to_list())
        ],
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
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token, scopes=Scopes.COMMUNITY_SUPERUSER.to_list())
        ],
):
    # Create the community
    try:
        db_community = await communities.create_new_community(
            db, community,
            by=(token.user.username if token.user else "Web Token")
        )
    except AlreadyExistsError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admin is already owner of a community. Transfer ownership first."
        )

    return db_community


@router.get("/me", response_model=schemas.CommunityWithRelations)
async def get_own_community(
        community: Annotated[
            schemas.CommunityWithRelations,
            Security(get_active_token_community(True), scopes=Scopes.COMMUNITY_READ)
        ]
):
    return community


@router.put("/me/owner")
async def transfer_own_community_ownership(
        db: DatabaseDep,
        admin: AdminDep,
        community: Annotated[
            schemas.Community,
            Security(get_active_token_community(False), scopes=Scopes.COMMUNITY_MANAGE)
        ],
        token: Annotated[
            web_schemas.TokenWithHash,
            Depends(get_active_token)
        ],
) -> bool:
    return await transfer_community_ownership(db, community, admin, token)


@router.get("/{community_id}", response_model=schemas.SafeCommunityWithRelations)
async def get_community(
        community: CommunityWithRelationsDep,
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token, scopes=Scopes.COMMUNITY_READ.to_list())
        ],
):
    return community

@router.put("/{community_id}/owner")
async def transfer_community_ownership(
        db: DatabaseDep,
        community: CommunityDep,
        admin: AdminDep,
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token, scopes=Scopes.COMMUNITY_SUPERUSER.to_list())
        ],
) -> bool:
    try:
        return await communities.transfer_ownership(
            db, community, admin,
            by=(token.user.username if token.user else "Web Token")
        )
    except AdminNotAssociatedError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admin is not part of the community"
        )

def setup(app: FastAPI):
    app.include_router(router)
