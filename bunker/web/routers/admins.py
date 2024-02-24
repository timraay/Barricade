from fastapi import FastAPI, APIRouter, HTTPException, status

from bunker import schemas
from bunker.crud import communities
from bunker.exceptions import AlreadyExistsError, TooManyAdminsError, NotFoundError, AdminOwnsCommunityError
from bunker.db import DatabaseDep
from bunker.web.paginator import PaginatorDep, PaginatedResponse
from bunker.web.routers.communities import AdminDep, CommunityDep

router = APIRouter(prefix="/admins")


@router.get("", response_model=PaginatedResponse[schemas.AdminRef])
async def get_all_admins(
        db: DatabaseDep,
        paginator: PaginatorDep,
):
    result = await communities.get_all_admins(db,
        limit=paginator.limit,
        offset=paginator.offset,
    )
    return paginator.paginate(result)

@router.post("", response_model=schemas.AdminRef)
async def create_admin(
        db: DatabaseDep,
        admin: schemas.AdminCreateParams,
):
    # Create the community
    try:
        db_admin = await communities.create_new_admin(db, admin)
    except AlreadyExistsError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="An admin with this ID already exists"
        )
    except TooManyAdminsError:
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


@router.put("/{admin_id}/join", response_model=schemas.Admin)
async def admin_join_community(
        db: DatabaseDep,
        admin: AdminDep,
        community: CommunityDep,
):
    try:
        return await communities.admin_join_community(db, admin, community)
    except AlreadyExistsError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admin is already part of a community"
        )
    except TooManyAdminsError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Community is at admin limit",
        )


@router.put("/{admin_id}/leave", response_model=schemas.Admin)
async def admin_leave_community(
        db: DatabaseDep,
        admin: AdminDep,
):
    try:
        return await communities.admin_leave_community(db, admin)
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



def setup(app: FastAPI):
    app.include_router(router)
