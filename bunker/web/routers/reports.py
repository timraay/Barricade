from fastapi import FastAPI, APIRouter, Depends, HTTPException, status
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bunker import schemas
from bunker.crud.reports import get_token_by_value, create_report, get_report_by_id, get_all_reports
from bunker.enums import ReportReasonFlag
from bunker.forwarding import forward_report_to_communities, forward_report_to_token_owner
from bunker.db import models, get_db

router = APIRouter(prefix="/reports")

@router.get("", response_model=list[schemas.Report])
async def get_reports(
        db: AsyncSession = Depends(get_db)
):
    return await get_all_reports(db)

@router.post("/submit", response_model=schemas.ReportWithToken)
async def submit_report(
        submission: schemas.ReportSubmission,
        db: AsyncSession = Depends(get_db)
):
    # Validate the token
    token = await get_token_by_value(db, submission.data.token)
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
    if token.report:
        logging.warn("Token was already used")
        raise invalid_token_error
    
    reasons_bitflag, reasons_custom = ReportReasonFlag.from_list(submission.data.reasons)

    # Create the report
    report = schemas.ReportCreateParams(
        **submission.data.model_dump(exclude={"token", "reasons"}),
        token=token,
        reasons_bitflag=reasons_bitflag,
        reasons_custom=reasons_custom,
    )
    db_report = await create_report(db, report)
    db_report.token = token
    return db_report
        
@router.get("/forward", response_model=schemas.ReportWithToken)
async def forward_report(
        report_id: int,
        db: AsyncSession = Depends(get_db)
):
    db_report = await get_report_by_id(db, report_id, load_relations=True)
    if not db_report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report does not exist"
        )
    
    await forward_report_to_communities(db_report)
    await forward_report_to_token_owner(db_report)
    return db_report


@router.get("/{report_id}", response_model=schemas.ReportWithToken)
async def get_reports(
        report_id: int,
        db: AsyncSession = Depends(get_db),
):
    report = await get_report_by_id(db, report_id=report_id, load_relations=True)
    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No report with this ID"
        )
    return report


def setup(app: FastAPI):
    app.include_router(router)
