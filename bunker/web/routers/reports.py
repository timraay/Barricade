from fastapi import FastAPI, APIRouter, HTTPException, status
import logging

from bunker import schemas
from bunker.web.paginator import PaginatedResponse, PaginatorDep
from bunker.crud import reports
from bunker.enums import ReportReasonFlag
from bunker.forwarding import forward_report_to_communities, forward_report_to_token_owner
from bunker.db import DatabaseDep

router = APIRouter(prefix="/reports")

@router.get("", response_model=PaginatedResponse[schemas.Report])
async def get_reports(
        db: DatabaseDep,
        paginator: PaginatorDep,
):
    result = await reports.get_all_reports(db,
        limit=paginator.limit,
        offset=paginator.offset
    )
    return paginator.paginate(result)
    

@router.post("/submit", response_model=schemas.ReportWithRelations)
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
    db_report = await reports.create_report(db, report)
    db_report.token = token
    return db_report
        
@router.get("/forward", response_model=schemas.ReportWithRelations)
async def forward_report(
        report_id: int,
        db: DatabaseDep,
):
    db_report = await reports.get_report_by_id(db, report_id, load_relations=True)
    if not db_report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report does not exist"
        )
    
    await forward_report_to_communities(db_report)
    await forward_report_to_token_owner(db_report)
    return db_report


@router.get("/{report_id}", response_model=schemas.ReportWithRelations)
async def get_reports(
        report_id: int,
        db: DatabaseDep,
):
    report = await reports.get_report_by_id(db, report_id=report_id, load_relations=True)
    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No report with this ID"
        )
    return report


def setup(app: FastAPI):
    app.include_router(router)
