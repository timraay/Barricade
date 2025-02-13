from typing import Annotated
from fastapi import Depends, FastAPI, APIRouter, HTTPException, Security, status

from barricade import schemas
from barricade.crud import communities
from barricade.exceptions import AlreadyExistsError, MaxLimitReachedError, NotFoundError, AdminOwnsCommunityError
from barricade.db import DatabaseDep, models
from barricade.web import schemas as web_schemas
from barricade.web.paginator import PaginatorDep, PaginatedResponse
from barricade.web.routers.communities import AdminDep, AdminWithRelationsDep, CommunityDep
from barricade.web.scopes import Scopes
from barricade.web.security import get_active_token, get_active_token_community, get_active_token_of_community

router = APIRouter(prefix="", tags=["Admins"])


@router.get("/admins", response_model=PaginatedResponse[schemas.AdminRef])
async def get_all_admins(
        db: DatabaseDep,
        paginator: PaginatorDep,
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token, scopes=Scopes.COMMUNITY_READ.to_list())
        ],
):
    result = await communities.get_all_admins(db,
        limit=paginator.limit,
        offset=paginator.offset,
    )
    return paginator.paginate(result)


@router.post("/admins", response_model=schemas.AdminRef)
async def create_admin(
        db: DatabaseDep,
        admin: schemas.AdminCreateParams,
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token, scopes=Scopes.COMMUNITY_MANAGE.to_list())
        ],
):
    # Create the community
    try:
        db_admin = await communities.create_new_admin(
            db, admin,
            by=(token.user.username if token.user else "Web Token")
        )
    except AlreadyExistsError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An admin with this ID already exists"
        )
    except MaxLimitReachedError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Community is at admin limit",
        )
    except NotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Community does not exist"
        )

    return db_admin


@router.get("/admins/{admin_id}", response_model=schemas.Admin)
async def get_admin(
        admin: AdminWithRelationsDep,
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token, scopes=Scopes.COMMUNITY_READ.to_list())
        ],
):
    return admin    


@router.put("/admins/{admin_id}/join", response_model=schemas.Admin)
async def admin_join_community(
        db: DatabaseDep,
        admin: AdminDep,
        community: CommunityDep,
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token, scopes=Scopes.COMMUNITY_MANAGE.to_list())
        ],
):
    try:
        return await communities.admin_join_community(
            db, admin, community,
            by=(token.user.username if token.user else "Web Token")
        )
    except AlreadyExistsError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admin is already part of a community"
        )
    except MaxLimitReachedError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Community is at admin limit",
        )


@router.put("/admins/{admin_id}/leave", response_model=schemas.Admin)
async def admin_leave_community(
        db: DatabaseDep,
        admin: AdminDep,
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token, scopes=Scopes.COMMUNITY_MANAGE.to_list())
        ],
):
    try:
        return await communities.admin_leave_community(
            db, admin,
            by=(token.user.username if token.user else "Web Token")
        )
    except NotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Admin is not part of a community"
        )
    except AdminOwnsCommunityError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Community is at admin limit",
        )


@router.post("/communities/me/admins", response_model=schemas.AdminRef)
async def create_admin_for_own_community(
        db: DatabaseDep,
        admin: schemas.AdminCreateParams,
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token_of_community, scopes=Scopes.COMMUNITY_ME_MANAGE.to_list())
        ],
):
    if admin.community_id != token.community_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin is not part of your community"
        )
    return await create_admin(db, admin, token)


@router.put("/communities/me/admins/join", response_model=schemas.Admin)
async def admin_join_own_community(
    db: DatabaseDep,
    admin: AdminDep,
    community: Annotated[
        models.Community,
        Security(get_active_token_community(False), scopes=Scopes.COMMUNITY_ME_MANAGE.to_list())
    ],
    token: Annotated[
        web_schemas.TokenWithHash,
        Depends(get_active_token_of_community)
    ]
):
    return await admin_join_community(db, admin, community, token)


@router.put("/communities/me/admins/leave", response_model=schemas.Admin)
async def admin_leave_own_community(
    db: DatabaseDep,
    admin: AdminDep,
    token: Annotated[
        web_schemas.TokenWithHash,
        Security(get_active_token_of_community, scopes=Scopes.COMMUNITY_ME_MANAGE.to_list())
    ]
):
    if admin.community_id != token.community_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin is not part of your community"
        )
    return await admin_leave_community(db, admin, token)



def setup(app: FastAPI):
    app.include_router(router)
