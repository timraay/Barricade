from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bunker import schemas
from bunker.db import models
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
