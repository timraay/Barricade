from fastapi import FastAPI, APIRouter, Depends, HTTPException, status
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bunker import schemas
from bunker.reports import get_token_data, create_report, forward_report_to_communities
from bunker.db import models, get_db

router = APIRouter(prefix="/reports")


@router.get("", response_model=list[schemas.Report])
async def get_all_reports(
        db: AsyncSession = Depends(get_db)
):
    stmt = select(models.Report)
    result = await db.execute(stmt)
    return result.scalars().all()

@router.post("/submit", response_model=schemas.Report)
async def submit_report(
        submission: schemas.ReportSubmission,
        db: AsyncSession = Depends(get_db)
):
    # Validate the token
    token_data = await get_token_data(db, submission.data.token, load_relations=True)
    invalid_token_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid token"
    )
    if not token_data:
        logging.warn("Token not found")
        raise invalid_token_error
    if token_data.is_expired():
        logging.warn("Token has expired")
        raise invalid_token_error
    if token_data.report:
        logging.warn("Token was already used")
        raise invalid_token_error

    # Create the report
    report = schemas.ReportCreateParams(
        timestamp=submission.timestamp,
        body=submission.data.description,
        token=token_data,
        reasons=submission.data.reasons,
        players=submission.data.players,
        attachment_urls=submission.data.attachments,
    )
    db_report = await create_report(db, report)
    return db_report
        
@router.get("/forward", response_model=schemas.Report)
async def forward_report(
        report_id: int,
        db: AsyncSession = Depends(get_db)
):
    db_report = await db.get(models.Report, report_id)
    if not db_report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report does not exist"
        )
    
    await forward_report_to_communities(db_report, schemas.DiscordMessagePayload())
    return db_report


def setup(app: FastAPI):
    app.include_router(router)
