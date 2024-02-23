from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from bunker import schemas
from bunker.db import models
from bunker.enums import ReportRejectReason
from bunker.hooks import EventHooks

async def set_report_response(db: AsyncSession, prr: schemas.ResponseCreateParams):
    stmt = select(models.PlayerReportResponse).where(
        models.PlayerReportResponse.pr_id == prr.pr_id,
        models.PlayerReportResponse.community_id == prr.community_id,
    ).limit(1)
    db_prr = await db.scalar(stmt)

    if not db_prr:
        db_prr = models.PlayerReportResponse(**prr.model_dump())
        db.add(db_prr)
        await db.commit()
        await db.refresh(db_prr)

    else:
        db_prr.banned = prr.banned
        db_prr.reject_reason = prr.reject_reason
        await db.commit()

    prr = schemas.Response.model_validate(db_prr)
    if prr.banned:
        EventHooks.invoke_player_ban(prr)
    else:
        EventHooks.invoke_player_unban(prr)

    return db_prr

async def get_community_responses_to_report(db: AsyncSession, report: schemas.Report, community_id: int):
    stmt = select(models.PlayerReportResponse).where(
        models.PlayerReportResponse.community_id == community_id,
        models.PlayerReportResponse.player_report.in_([
            pr.id for pr in report.players
        ])
    )
    result = await db.scalars(stmt)
    return result.all()

async def get_response_stats(db: AsyncSession, player_report: schemas.PlayerReport):
    stmt = select(
        models.PlayerReportResponse.banned,
        models.PlayerReportResponse.reject_reason,
        func.count(models.PlayerReportResponse.pr_id).label("amount")
    ).where(
        models.PlayerReportResponse.pr_id == player_report.id
    ).group_by(
        models.PlayerReportResponse.banned,
        models.PlayerReportResponse.reject_reason,
    ).having(or_(
        models.PlayerReportResponse.banned.is_(True),
        models.PlayerReportResponse.reject_reason.is_not(None),
    ))

    results = await db.execute(stmt)
    data = schemas.ResponseStats(
        num_banned=0,
        num_rejected=0,
        reject_reasons={
            reject_reason: 0
            for reject_reason in ReportRejectReason
        }
    )

    for result in results:
        if result.banned:
            data.num_banned = result.amount
        else:
            data.num_rejected += result.amount
            data.reject_reasons[result.reject_reason] += result.amount

    return data
