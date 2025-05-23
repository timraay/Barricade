from typing import Sequence
from sqlalchemy import exists, not_, select, func
import sqlalchemy.exc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from barricade import schemas
from barricade.db import models
from barricade.enums import ReportReasonFlag, ReportRejectReason
from barricade.exceptions import NotFoundError
from barricade.hooks import EventHooks
from barricade.logger import get_logger

async def set_report_response(db: AsyncSession, params: schemas.ResponseCreateParams):
    """Set or change a community's response to a reported player.

    This immediately commits the transaction.

    Parameters
    ----------
    db : AsyncSession
        An asynchronous database session
    params : schemas.ResponseCreateParams
        Payload

    Returns
    -------
    models.PlayerReportResponse
        The response
    """
    stmt = select(models.PlayerReportResponse).where(
        models.PlayerReportResponse.pr_id == params.pr_id,
        models.PlayerReportResponse.community_id == params.community_id,
    ).options(
        selectinload(models.PlayerReportResponse.player_report)
            .selectinload(models.PlayerReport.report)
            .selectinload(models.Report.token)
    ).limit(1)
    db_prr = await db.scalar(stmt)

    if not db_prr:
        db_prr = models.PlayerReportResponse(**params.model_dump())
        db.add(db_prr)
        try:
            await db.flush()
        except sqlalchemy.exc.IntegrityError:
            raise NotFoundError("Report or community no longer exists")
        await db.commit()
        await db.refresh(db_prr)
        await db_prr.player_report.report.awaitable_attrs.token

    else:
        db_prr.banned = params.banned
        db_prr.reject_reason = params.reject_reason
        await db.commit()

    prr = schemas.ResponseWithToken.model_validate(db_prr)
    if prr.banned:
        EventHooks.invoke_player_ban(prr)
    else:
        EventHooks.invoke_player_unban(prr)
    
    logger = get_logger(prr.community_id)
    logger.info(
        "Set report response for player %s of report %s. Banned? %s. Reject reason? %s",
        prr.player_report.player_id, prr.player_report.report_id, prr.banned, prr.reject_reason,
    )

    return db_prr

async def get_community_responses_to_report(db: AsyncSession, report: schemas.Report, community_id: int):
    """Get all of a community's responses to a specific report.

    Parameters
    ----------
    db : AsyncSession
        An asynchronous database session
    report : schemas.Report
        The report whose responses to obtain
    community_id : int
        The ID of the community who the responses should belong to

    Returns
    -------
    Sequence[models.PlayerReportResponse]
        All of a community's responses to the report
    """
    stmt = select(models.PlayerReportResponse).where(
        models.PlayerReportResponse.community_id == community_id,
        models.PlayerReportResponse.pr_id.in_([
            pr.id for pr in report.players
        ])
    ).options(
        selectinload(models.PlayerReportResponse.player_report)
            .selectinload(models.PlayerReport.report)
            .selectinload(models.Report.token)
    )
    result = await db.scalars(stmt)
    return result.all()

async def get_response_stats(db: AsyncSession, player_report: schemas.PlayerReportRef) -> schemas.ResponseStats:
    stmt = select(
        models.PlayerReportResponse.banned,
        models.PlayerReportResponse.reject_reason,
        func.count(models.PlayerReportResponse.pr_id).label("amount")
    ).where(
        models.PlayerReportResponse.pr_id == player_report.id
    ).group_by(
        models.PlayerReportResponse.banned,
        models.PlayerReportResponse.reject_reason,
    )

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
            if result.reject_reason:
                data.reject_reasons[result.reject_reason] += result.amount

    return data

async def bulk_get_response_stats(db: AsyncSession, players: Sequence[schemas.PlayerReportRef]) -> dict[int, schemas.ResponseStats]:
    stats: dict[int, schemas.ResponseStats] = {}
    for player in players:
        stats[player.id] = await get_response_stats(db, player)
    return stats

async def get_pending_responses(
        db: AsyncSession,
        community: schemas.CommunityRef,
        player_reports: list[schemas.PlayerReportRef],
):
    responses = {
        pr.id: schemas.PendingResponse(
            pr_id=pr.id,
            player_report=pr,
            community_id=community.id,
            community=community,
        ) for pr in player_reports
    }
    
    stmt = select(
        models.PlayerReportResponse.pr_id,
        models.PlayerReportResponse.reject_reason,
        models.PlayerReportResponse.banned,
        models.PlayerReportResponse.responded_by,
    ).join(
        models.PlayerReport
    ).where(
        models.PlayerReportResponse.community_id == community.id,
        models.PlayerReport.id.in_(
            [pr.id for pr in player_reports]
        )
    ).limit(len(player_reports))
    result = await db.execute(stmt)
    for row in result:
        response = responses[row.pr_id]

        response.banned = row.banned
        response.reject_reason = row.reject_reason
        response.responded_by = row.responded_by
    
    return list(responses.values())

async def get_reports_for_player_with_no_community_review(
        db: AsyncSession,
        player_id: str,
        community_id: int,
        reasons_filter: ReportReasonFlag | None = None
):
    """Get all reports of a specific player which the given community has not yet responded to.

    Parameters
    ----------
    db : AsyncSession
        An asynchronous database session
    player_id : str
        The ID of the player
    community_id : int
        The ID of the community
    reasons_filter : ReportReasonFlag | None
        Filter out reports whose reasons do not overlap with the filter. If None, no filter
        will be applied. By default None.

    Returns
    -------
    Sequence[models.Report]
        A sequence of report models
    """
    options = (selectinload(models.Report.players), selectinload(models.Report.token))
    stmt = select(models.Report) \
        .join(models.Report.players) \
        .join(models.Report.token) \
        .where(
            models.PlayerReport.player_id == player_id,
            models.ReportToken.community_id != community_id,
            not_(
                exists().where(
                    models.PlayerReportResponse.community_id == community_id,
                    models.PlayerReportResponse.pr_id == models.PlayerReport.id
                )
            )
        ) \
        .options(*options)
    
    if reasons_filter is not None:
        stmt = stmt.where(
            models.Report.reasons_bitflag.bitwise_and(reasons_filter) != 0
        )

    result = await db.scalars(stmt)
    return result.all()

async def get_successful_responses_without_bans(db: AsyncSession, community_id: int, integration_id: int):
    """Find all players that an integration has not banned yet, that should
    be banned. Returns one response with token for each player found.

    If multiple responses to the same player exist, one is arbitrarly picked.

    Parameters
    ----------
    db : AsyncSession
        An asynchronous database session
    community_id : int
        The ID of the community
    integration_id : int
        The ID of the integration

    Returns
    -------
    Sequence[models.PlayerReportResponse]
        A sequence of responses, with the report token included
    """
    stmt = (
        select(models.PlayerReportResponse)
        .where(
            models.PlayerReportResponse.community_id == community_id,
            models.PlayerReportResponse.banned.is_(True),
        )
        .join(models.PlayerReportResponse.player_report)
        .where(
            not_(exists(
                select(models.PlayerBan)
                .where(
                    models.PlayerBan.integration_id == integration_id,
                    models.PlayerBan.player_id == models.PlayerReport.player_id,
                )
            ))
        )
        .distinct(models.PlayerReport.player_id)
        .options(
            selectinload(models.PlayerReportResponse.player_report)
            .selectinload(models.PlayerReport.report)
            .selectinload(models.Report.token)
        )
    )
    
    result = await db.scalars(stmt)
    return result.all()