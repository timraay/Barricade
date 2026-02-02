from contextlib import asynccontextmanager
import discord
from fastapi import Depends, FastAPI, APIRouter, HTTPException, Security, status
from io import BytesIO
import logging
from typing import Annotated, Literal

from barricade import schemas
from barricade.crud import communities, reports
from barricade.db import DatabaseDep, models
from barricade.discord.bot import bot
from barricade.enums import Emojis, ReportReasonFlag
from barricade.exceptions import NotFoundError
from barricade.web import schemas as web_schemas
from barricade.web.paginator import PaginatedResponse, PaginatorDep
from barricade.web.scopes import Scopes
from barricade.web.security import get_active_token, get_active_token_of_community

logger = logging.getLogger("uvicorn.error")

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


@router.get("/reports", response_model=PaginatedResponse[schemas.SafeReportWithToken])
async def get_reports(
        db: DatabaseDep,
        paginator: PaginatorDep,
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token, scopes=Scopes.REPORT_READ.to_list())
        ],
):
    result = await reports.get_all_reports(db,
        load_token=True,
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
        logger.warning("Token %s not found. Response ID: %s", submission.data.token, submission.id)
        raise invalid_token_error
    
    if token.is_expired():
        logger.warning("Token %s has expired. Response ID: %s", submission.data.token, submission.id)
        raise invalid_token_error
    
    return token

@asynccontextmanager
async def notify_of_errors_in_dms(token: models.ReportToken, submission: schemas.ReportSubmission):
    try:
        yield
    except Exception as e:
        try:
            user = await bot.get_or_fetch_user(token.admin_id)

            file = discord.File(
                BytesIO(submission.model_dump_json(indent=2).encode(encoding="utf-8")),
                filename=f"submission-{submission.id}.json"
            )

            content = (
                "### **Your report couldn't be submitted!**"
                "\nAn unexpected error happened while submitting your report. Please try again or reach out to support."
                "\n"
                f"\n{Emojis.TICK_NO} `{type(e).__name__}: {e}`"
                "\n"
                "\n-# Details of your submission are attached below)"
            )

            await user.send(content=content, file=file)
        
        except Exception:
            logger.exception("Failed to notify %s of submission failure", token.admin_id)
            pass

        raise e

@router.post("/reports/submit", response_model=schemas.SafeReportWithToken)
async def submit_report(
        token: Annotated[models.ReportToken, Depends(validate_submission_token)],
        submission: schemas.ReportSubmission,
        db: DatabaseDep,
):
    async with notify_of_errors_in_dms(token, submission):
        if token.report:
            logger.warning("Token %s has already been used. Response ID: %s", submission.data.token, submission.id)
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
        logger.warning("Token %s has not been used yet. Response ID: %s", submission.data.token, submission.id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token"
        )
        
    async with notify_of_errors_in_dms(token, submission):
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
) -> Literal[True]:
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

@router.get("/communities/me/reports", response_model=PaginatedResponse[schemas.SafeReportWithToken])
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
        load_token=True,
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
