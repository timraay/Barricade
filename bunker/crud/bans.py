from typing import Sequence
from sqlalchemy import exists, select, delete, not_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, joinedload

from bunker import schemas
from bunker.db import models
from bunker.exceptions import AlreadyExistsError

async def get_ban_by_id(db: AsyncSession, ban_id: int, load_relations: bool = False):
    """Look up a ban by its ID.

    Parameters
    ----------
    db : AsyncSession
        An asynchronous database session
    ban_id : int
        The ID of the ban
    load_relations : bool, optional
        Whether to also load relational properties, by default False

    Returns
    -------
    PlayerBan | None
        The admin model, or None if it does not exist
    """
    if load_relations:
        options = (selectinload("*"),)
    else:
        options = ()

    return await db.get(models.PlayerBan, ban_id, options=options)

async def get_ban_by_player_and_integration(db: AsyncSession, player_id: str, integration_id: int, load_relations: bool = False):
    stmt = select(models.PlayerBan).where(
        models.PlayerBan.player_id == player_id,
        models.PlayerBan.integration_id == integration_id
    )
    if load_relations:
        stmt = stmt.options(selectinload("*"))
    return await db.scalar(stmt)

async def get_bans_by_integration(db: AsyncSession, integration_id: int):
    stmt = select(models.PlayerBan).where(
        models.PlayerBan.integration_id == integration_id
    )
    result = await db.stream_scalars(stmt)
    async for db_ban in result:
        yield db_ban

async def get_player_bans_for_community(db: AsyncSession, player_id: str, community_id: int):
    stmt = select(models.PlayerBan).join(models.PlayerBan.integration).where(
        models.PlayerBan.player_id == player_id,
        models.Integration.community_id == community_id,
    ).options(joinedload(models.PlayerBan.integration))
    result = await db.scalars(stmt)
    return result.all()

async def create_ban(db: AsyncSession, ban: schemas.PlayerBanCreateParams):
    db_ban = models.PlayerBan(**ban.model_dump())
    db.add(db_ban)
    try:
        await db.flush()
    except IntegrityError:
        raise AlreadyExistsError("Player is already banned")
    return db_ban

async def bulk_create_bans(db: AsyncSession, bans: list[schemas.PlayerBanCreateParams]):
    stmt = insert(models.PlayerBan).values(
        [ban.model_dump() for ban in bans]
    ).on_conflict_do_nothing(
        index_elements=["player_id", "integration_id"]
    )
    await db.execute(stmt)
    await db.flush()

async def bulk_delete_bans(db: AsyncSession, *where_clauses):
    stmt = delete(models.PlayerBan).where(*where_clauses)
    await db.execute(stmt)
    await db.flush()

async def get_player_bans_without_responses(db: AsyncSession, player_ids: Sequence[str], community_id: int | None = None):
    """Get a list of player bans whose community has not responded to any reports
    or has not chosen to ban them.

    Essentially, returns a list of bans that should no longer exist.

    Parameters
    ----------
    db : AsyncSession
        An asynchronous database session
    player_ids : Sequence[str]
        A list of player IDs
    community_id : int | None, optional
        The ID of a community to filter results by, by default None

    Returns
    -------
    Sequence[PlayerBan]
        A list of player bans
    """
    stmt = select(models.PlayerBan) \
        .join(models.PlayerBan.integration) \
        .where(
            models.PlayerBan.player_id.in_(player_ids),
            not_(exists(
                select(models.PlayerReportResponse)
                    .join(models.PlayerReport)
                    .where(
                        models.PlayerReport.player_id == models.PlayerBan.player_id,
                        models.PlayerReportResponse.community_id == models.Integration.community_id,
                        models.PlayerReportResponse.banned.is_(True),
                    )
            ))
        )
    
    if community_id is not None:
        stmt = stmt.where(models.Integration.community_id == community_id)
    
    result = await db.scalars(stmt)
    return result.all()

