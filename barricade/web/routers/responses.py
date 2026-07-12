import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, FastAPI, Security

from barricade import schemas
from barricade.crud import responses
from barricade.db import DatabaseDep
from barricade.web import schemas as web_schemas
from barricade.web.paginator import PaginatedResponse, PaginatorDep
from barricade.web.scopes import Scopes
from barricade.web.security import (
    get_active_token,
    get_active_token_community,
)

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="", tags=["Responses"])


@router.get("/responses", response_model=PaginatedResponse[schemas.Response])
async def get_responses(
    db: DatabaseDep,
    paginator: PaginatorDep,
    token: Annotated[
        web_schemas.TokenWithHash,
        Security(
            get_active_token,
            scopes=(Scopes.REPORT_READ | Scopes.COMMUNITY_READ).to_list(),
        ),
    ],
    community_id: int | None = None,
    pr_id: int | None = None,
    report_id: int | None = None,
    responded_before: datetime | None = None,
    responded_after: datetime | None = None,
):
    result = await responses.get_all_responses(
        db,
        load_token=False,
        limit=paginator.limit,
        offset=paginator.offset,
        community_id=community_id,
        pr_id=pr_id,
        report_id=report_id,
        responded_before=responded_before,
        responded_after=responded_after,
    )
    return paginator.paginate(result)


@router.get(
    "/communities/me/responses",
    response_model=PaginatedResponse[schemas.SafeReportWithToken],
)
async def get_own_responses(
    db: DatabaseDep,
    paginator: PaginatorDep,
    community: Annotated[
        schemas.Community,
        Security(
            get_active_token_community,
            scopes=(Scopes.REPORT_ME_READ | Scopes.COMMUNITY_ME_READ).to_list(),
        ),
    ],
    pr_id: int | None = None,
    report_id: int | None = None,
    responded_before: datetime | None = None,
    responded_after: datetime | None = None,
):
    result = await responses.get_all_responses(
        db,
        load_token=False,
        limit=paginator.limit,
        offset=paginator.offset,
        community_id=community.id,
        pr_id=pr_id,
        report_id=report_id,
        responded_before=responded_before,
        responded_after=responded_after,
    )
    return paginator.paginate(result)


def setup(app: FastAPI):
    app.include_router(router)
