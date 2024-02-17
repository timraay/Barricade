from sqlalchemy import select, delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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

async def create_ban(db: AsyncSession, ban: schemas.PlayerBanCreateParams):
    db_ban = models.PlayerBan(ban.model_dump())
    db.add(db_ban)
    try:
        await db.commit()
    except IntegrityError:
        raise AlreadyExistsError("Player is already banned")
    return db_ban

async def bulk_create_bans(db: AsyncSession, bans: list[schemas.PlayerBanCreateParams]):
    stmt = insert(models.PlayerBan).values(bans).on_conflict_do_nothing(
        index_elements=["player_id", "integration_id"]
    )
    await db.execute(stmt)
    await db.commit()

async def bulk_delete_bans(db: AsyncSession, *where_clauses):
    stmt = delete(models.PlayerBan).where(*where_clauses)
    await db.execute(stmt)
    await db.commit()
