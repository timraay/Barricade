from typing import Annotated
from fastapi import Depends, FastAPI, APIRouter, HTTPException, Security, status
import logging

from barricade import schemas
from barricade.crud import communities, reports
from barricade.db import DatabaseDep, models
from barricade.enums import ReportReasonFlag
from barricade.exceptions import NotFoundError
from barricade.web import schemas as web_schemas
from barricade.web.paginator import PaginatedResponse, PaginatorDep
from barricade.web.scopes import Scopes
from barricade.web.security import get_active_token, get_active_token_of_community

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

@router.post("/reports", response_model=schemas.SafeReportWithToken)
async def create_report(
        report: schemas.ReportCreateParamsTokenless,
        db: DatabaseDep,
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token, scopes=Scopes.REPORT_MANAGE.to_list())
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
    
    db_token = await reports.create_token(db,
        params=schemas.ReportTokenCreateParams(
            admin_id=db_admin.discord_id,
            community_id=db_admin.community_id,
            platform=report.platform,
        ),
        by=(token.user.username if token.user else "Web Token")
    )

    return await reports.create_report(db,
        params=schemas.ReportCreateParams(
            **report.model_dump(exclude={"admin_id"}),
            token_id=db_token.id,
        ),
        by=(token.user.username if token.user else "Web Token"),
    )

async def validate_submission_token(
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
        logging.warn("Token %s not found. Response ID: %s", submission.data.token, submission.id)
        raise invalid_token_error
    
    if token.is_expired():
        logging.warn("Token %s has expired. Response ID: %s", submission.data.token, submission.id)
        raise invalid_token_error
    
    return token

@router.post("/reports/submit", response_model=schemas.SafeReportWithToken)
async def submit_report(
        token: Annotated[models.ReportToken, Depends(validate_submission_token)],
        submission: schemas.ReportSubmission,
        db: DatabaseDep,
):
    if token.report:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token"
        )
    
    reasons_bitflag, reasons_custom = ReportReasonFlag.from_list(submission.data.reasons)

    # Create the report
    report = schemas.ReportCreateParams(
        **submission.data.model_dump(exclude={"token", "reasons"}),
        token_id=token.id,
        reasons_bitflag=reasons_bitflag,
        reasons_custom=reasons_custom,
    )

    db_report = await reports.create_report(db, report)

    return db_report

@router.put("/reports/submit", response_model=schemas.SafeReportWithToken)
async def submit_report_edit(
        token: Annotated[models.ReportToken, Depends(validate_submission_token)],
        submission: schemas.ReportSubmission,
        db: DatabaseDep,
):
    if not token.report:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token"
        )
    
    reasons_bitflag, reasons_custom = ReportReasonFlag.from_list(submission.data.reasons)

    # Create the report
    report = schemas.ReportCreateParams(
        **submission.data.model_dump(exclude={"token", "reasons"}),
        token_id=token.id,
        reasons_bitflag=reasons_bitflag,
        reasons_custom=reasons_custom,
    )
    
    db.expire_all()
    db_report = await reports.edit_report(db, report)
    await db.commit()

    return db_report
        
@router.get("/reports/{report_id}", response_model=schemas.SafeReportWithToken)
async def get_report(
        report: ReportWithTokenDep,
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token, scopes=Scopes.REPORT_READ.to_list())
        ],
):
    return report

@router.put("/reports/{report_id}", response_model=schemas.SafeReportWithToken)
async def edit_report(
        report_id: int,
        report: schemas.ReportEditParams,
        db: DatabaseDep,
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token, scopes=Scopes.REPORT_MANAGE.to_list())
        ],
):
    params = report.model_dump()
    params["token_id"] = report_id
    try:
        result = await reports.edit_report(db,
            report=schemas.ReportCreateParams.model_validate(params),
            by=(token.user.username if token.user else "Web Token"),
        )
    except NotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report does not exist"
        )

    return result

@router.delete("/reports/{report_id}")
async def delete_report(
        report_id: int,
        db: DatabaseDep,
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token, scopes=Scopes.REPORT_MANAGE.to_list())
        ],
):
    try:
        return await reports.delete_report(db,
            report_id=report_id,
            by=(token.user.username if token.user else "Web Token"),
        )
    except NotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report does not exist"
        )

@router.get("/communities/me/reports", response_model=PaginatedResponse[schemas.Report])
async def get_own_reports(
        db: DatabaseDep,
        paginator: PaginatorDep,
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token_of_community, scopes=Scopes.REPORT_ME_READ.to_list())
        ],
):
    result = await reports.get_all_reports(db,
        community_id=token.community_id,
        limit=paginator.limit,
        offset=paginator.offset
    )
    return paginator.paginate(result)

@router.post("/communities/me/reports", response_model=schemas.SafeReportWithToken)
async def create_own_report(
        report: schemas.ReportCreateParamsTokenless,
        db: DatabaseDep,
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token_of_community, scopes=Scopes.REPORT_ME_MANAGE.to_list())
        ],
):
    admin = await communities.get_admin_by_id(db, report.admin_id)
    if not admin:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Admin does not exist"
        )
    if admin.community_id != token.community_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admin is not part of your community"
        )
    return await create_report(report, db, token)

@router.get("/communities/me/reports/{report_id}", response_model=schemas.SafeReportWithToken)
async def get_own_report(
        report: ReportWithTokenDep,
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token, scopes=Scopes.REPORT_ME_READ.to_list())
        ],
):
    if report.token.community_id != token.community_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Report was not created by your community"
        )
    return report

@router.put("/communities/me/reports/{report_id}", response_model=schemas.SafeReportWithToken)
async def edit_own_report(
        report: ReportWithTokenDep,
        params: schemas.ReportEditParams,
        db: DatabaseDep,
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token_of_community, scopes=Scopes.REPORT_ME_MANAGE.to_list())
        ],
):
    if report.token.community_id != token.community_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Report was not created by your community"
        )

    return await edit_report(report.id, params, db, token)

@router.delete("/communities/me/reports/{report_id}")
async def delete_own_report(
        report: ReportWithTokenDep,
        db: DatabaseDep,
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token_of_community, scopes=Scopes.REPORT_ME_MANAGE.to_list())
        ],
):
    if report.token.community_id != token.community_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Report was not created by your community"
        )

    return await delete_report(report.id, db, token)


def setup(app: FastAPI):
    app.include_router(router)
