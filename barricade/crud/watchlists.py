from sqlalchemy import exists, select, delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Load
from typing import Iterable

from barricade import schemas
from barricade.db import models
from barricade.exceptions import AlreadyExistsError

async def get_watchlist_by_id(db: AsyncSession, watchlist_id: int, load_relations: bool = False):
    """Look up a watchlist by its ID.

    Parameters
    ----------
    db : AsyncSession
        An asynchronous database session
    watchlist_id : int
        The ID of the watchlist
    load_relations : bool, optional
        Whether to also load relational properties, by default False

    Returns
    -------
    PlayerWatchlist | None
        The admin model, or None if it does not exist
    """
    if load_relations:
        options = (Load(models.PlayerWatchlist).selectinload("*"),)
    else:
        options = ()

    return await db.get(models.PlayerWatchlist, watchlist_id, options=options)

async def get_watchlist_by_player_and_community(db: AsyncSession, player_id: str, community_id: int, load_relations: bool = False):
    stmt = select(models.PlayerWatchlist).where(
        models.PlayerWatchlist.player_id == player_id,
        models.PlayerWatchlist.community_id == community_id
    )
    if load_relations:
        stmt = stmt.options(Load(models.PlayerWatchlist).selectinload("*"))
    return await db.scalar(stmt)

async def is_player_watchlisted(db: AsyncSession, player_id: str, community_id: int):
    stmt = select(exists().where(
        models.PlayerWatchlist.player_id == player_id,
        models.PlayerWatchlist.community_id == community_id,
    ))
    result = await db.scalar(stmt)
    return bool(result)

async def bulk_get_watchlists_by_player_and_community(db: AsyncSession, player_ids: Iterable[str], community_id: int, load_relations: bool = False):
    stmt = select(models.PlayerWatchlist).where(
        models.PlayerWatchlist.player_id.in_(player_ids),
        models.PlayerWatchlist.community_id == community_id
    )
    if load_relations:
        stmt = stmt.options(Load(models.PlayerWatchlist).selectinload("*"))
    result = await db.scalars(stmt)
    return result.all()

async def get_watchlists_by_community(db: AsyncSession, community_id: int):
    stmt = select(models.PlayerWatchlist).where(
        models.PlayerWatchlist.community_id == community_id
    )
    result = await db.stream_scalars(stmt)
    async for db_watchlist in result:
        yield db_watchlist

async def create_watchlist(db: AsyncSession, watchlist: schemas.PlayerWatchlistCreateParams):
    db_watchlist = models.PlayerWatchlist(**watchlist.model_dump())
    db.add(db_watchlist)
    try:
        await db.flush()
    except IntegrityError:
        raise AlreadyExistsError("Player is already watchlisted")
    return db_watchlist

async def bulk_create_watchlists(db: AsyncSession, watchlists: list[schemas.PlayerWatchlistCreateParams]):
    if not watchlists:
        return
    stmt = insert(models.PlayerWatchlist).values(
        [watchlist.model_dump() for watchlist in watchlists]
    )
    stmt = stmt.on_conflict_do_nothing(
        index_elements=["player_id", "community_id"]
    )
    await db.execute(stmt)
    await db.flush()

async def bulk_delete_watchlists(db: AsyncSession, *where_clauses):
    stmt = delete(models.PlayerWatchlist).where(*where_clauses)
    await db.execute(stmt)
    await db.flush()

async def filter_watchlisted_player_ids(db: AsyncSession, player_ids: Iterable[str], community_id: int):
    db_watchlists = await bulk_get_watchlists_by_player_and_community(db, player_ids, community_id)
    return {db_watchlist.player_id for db_watchlist in db_watchlists}
