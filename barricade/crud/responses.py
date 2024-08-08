from sqlalchemy import exists, not_, select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from barricade import schemas
from barricade.db import models
from barricade.enums import ReportRejectReason
from barricade.hooks import EventHooks

async def set_report_response(db: AsyncSession, prr: schemas.ResponseCreateParams):
    stmt = select(models.PlayerReportResponse).where(
        models.PlayerReportResponse.pr_id == prr.pr_id,
        models.PlayerReportResponse.community_id == prr.community_id,
    ).limit(1)
    db_prr = await db.scalar(stmt)

    if not db_prr:
        db_prr = models.PlayerReportResponse(**prr.model_dump())
        db.add(db_prr)
        await db.flush()
        await db.refresh(db_prr)

    else:
        db_prr.banned = prr.banned
        db_prr.reject_reason = prr.reject_reason
        await db.flush()

    prr = schemas.Response.model_validate(db_prr)
    if prr.banned:
        EventHooks.invoke_player_ban(prr)
    else:
        EventHooks.invoke_player_unban(prr)

    return db_prr

async def get_community_responses_to_report(db: AsyncSession, report: schemas.Report, community_id: int):
    stmt = select(models.PlayerReportResponse).where(
        models.PlayerReportResponse.community_id == community_id,
        models.PlayerReportResponse.pr_id.in_([
            pr.id for pr in report.players
        ])
    )
    result = await db.scalars(stmt)
    return result.all()

async def get_response_stats(db: AsyncSession, player_report: schemas.PlayerReportRef):
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

async def get_pending_responses(
        db: AsyncSession,
        community: schemas.CommunityRef,
        player_reports: list[schemas.PlayerReportRef],
):
    responses = {
        player.id: schemas.PendingResponse(
            pr_id=player.id,
            player_report=player,
            community_id=community.id,
            community=community,
        ) for player in player_reports
    }
    
    stmt = select(
        models.PlayerReportResponse.pr_id,
        models.PlayerReportResponse.reject_reason,
        models.PlayerReportResponse.banned
    ).join(
        models.PlayerReport
    ).where(
        models.PlayerReportResponse.community_id == community.id,
        models.PlayerReport.id.in_(
            [player.id for player in player_reports]
        )
    ).limit(len(player_reports))
    result = await db.execute(stmt)
    for row in result:
        responses[row.pr_id].banned = row.banned
        responses[row.pr_id].reject_reason = row.reject_reason
    
    return list(responses.values())

async def get_reports_for_player_with_no_community_response(db: AsyncSession, player_id: str, community_id: int):
    """Get all reports of a player

    Parameters
    ----------
    db : AsyncSession
        An asynchronous database session
    player_id : str
        The ID of the player
    load_token : bool, optional
        Whether to also load the relational token property, by default False

    Returns
    -------
    List[Report]
        A sequence of report models
    """
    options = (selectinload(models.Report.players), selectinload(models.Report.token))
    
    stmt = select(models.Report) \
        .join(models.Report.players) \
        .where(
            models.PlayerReport.player_id == player_id,
            not_(exists(models.PlayerReport.responses))
        ) \
        .options(*options)
    result = await db.scalars(stmt)
    return result.all()
