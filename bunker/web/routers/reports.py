from typing import Annotated
from fastapi import Depends, FastAPI, APIRouter, HTTPException, Security, status
import logging

from bunker import schemas
from bunker.crud import communities, reports
from bunker.db import DatabaseDep, models
from bunker.enums import ReportReasonFlag
from bunker.exceptions import NotFoundError
from bunker.web import schemas as web_schemas
from bunker.web.paginator import PaginatedResponse, PaginatorDep
from bunker.web.scopes import Scopes
from bunker.web.security import get_active_token, get_active_token_of_community

router = APIRouter(prefix="", tags=["Reports"])

def get_report_dependency(load_token: bool):
    async def inner(db: DatabaseDep, report_id: int):
        result = await reports.get_report_by_id(db, report_id, load_relations=load_token)
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Report does not exist"
            )
        return result
    return inner
ReportDep = Annotated[models.Report, Depends(get_report_dependency(False))]
ReportWithTokenDep = Annotated[models.Report, Depends(get_report_dependency(True))]


@router.get("/reports", response_model=PaginatedResponse[schemas.Report])
async def get_reports(
        db: DatabaseDep,
        paginator: PaginatorDep,
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token, scopes=Scopes.REPORT_READ.to_list())
        ],
):
    result = await reports.get_all_reports(db,
        limit=paginator.limit,
        offset=paginator.offset
    )
    return paginator.paginate(result)

@router.post("/reports", response_model=schemas.ReportWithToken)
async def create_report(
        report: schemas.ReportCreateParamsTokenless,
        db: DatabaseDep,
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token, scopes=Scopes.REPORT_SUPERUSER.to_list())
        ],
):
    db_admin = await communities.get_admin_by_id(db, report.admin_id)
    if not db_admin:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Admin does not exist"
        )
    if not db_admin.community_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admin is not part of a community"
        )
    
    db_token = await reports.create_token(
        db,
        token=schemas.ReportTokenCreateParams(
            admin_id=db_admin.discord_id,
            community_id=db_admin.community_id,
        ),
        by=(token.user.username if token.user else "Web Token")
    )

    return await reports.create_report(
        db,
        report=schemas.ReportCreateParams(
            **report.model_dump(exclude={"admin_id"}),
            token_id=db_token.id,
        ),
    )

@router.post("/reports/submit", response_model=schemas.ReportWithToken)
async def submit_report(
        submission: schemas.ReportSubmission,
        db: DatabaseDep,
):
    # Validate the token
    token = await reports.get_token_by_value(db, submission.data.token)
    invalid_token_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid token"
    )
    if not token:
        logging.warn("Token not found")
        raise invalid_token_error
    
    if token.is_expired():
        logging.warn("Token has expired")
        raise invalid_token_error
    
    reasons_bitflag, reasons_custom = ReportReasonFlag.from_list(submission.data.reasons)

    # Create the report
    report = schemas.ReportCreateParams(
        **submission.data.model_dump(exclude={"token", "reasons"}),
        token_id=token.id,
        reasons_bitflag=reasons_bitflag,
        reasons_custom=reasons_custom,
    )

    if token.report:
        db.expire_all()
        db_report = await reports.edit_report(db, report)
        await db.commit()
    else:
        db_report = await reports.create_report(db, report)

    return db_report
        
@router.get("/reports/{report_id}", response_model=schemas.ReportWithToken)
async def get_report(
        report: ReportWithTokenDep,
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token, scopes=Scopes.REPORT_READ.to_list())
        ],
):
    return report

@router.put("/reports/{report_id}", response_model=schemas.ReportWithToken)
async def edit_report(
        report_id: int,
        report: schemas.ReportEditParams,
        db: DatabaseDep,
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token, scopes=Scopes.REPORT_SUPERUSER.to_list())
        ],
):
    params = report.model_dump()
    params["token_id"] = report_id
    try:
        result = await reports.edit_report(
            db, schemas.ReportCreateParams.model_validate(params),
        )
    except NotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report does not exist"
        )

    return result


@router.post("/communities/me/reports", response_model=schemas.ReportWithToken)
async def create_own_report(
        report: schemas.ReportCreateParamsTokenless,
        db: DatabaseDep,
        token: Annotated[
            web_schemas.TokenWithHash,
            Depends(get_active_token_of_community)
        ],
):
    admin = await communities.get_admin_by_id(db, report.admin_id)
    if admin.community_id != token.community_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admin is not part of your community"
        )
    return await create_report(report, db, token)


@router.put("/communities/me/reports/{report_id}", response_model=schemas.ReportWithToken)
async def edit_own_report(
        report_id: int,
        report: schemas.ReportEditParams,
        db: DatabaseDep,
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token_of_community, scopes=Scopes.REPORT_MANAGE.to_list())
        ],
):
    db_report = await reports.get_report_by_id(db, report_id, load_token=True)
    if not db_report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report does not exist"
        )
    if db_report.token.community_id != token.community_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Report was not created by your community"
        )

    return await edit_report(report_id, report, db, token)


def setup(app: FastAPI):
    app.include_router(router)
