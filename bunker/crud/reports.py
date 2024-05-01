from datetime import datetime, timezone
import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bunker import schemas
from bunker.constants import REPORT_FORM_URL
from bunker.db import models
from bunker.discord.reports import get_report_embed, get_report_channel
from bunker.exceptions import NotFoundError, AlreadyExistsError
from bunker.hooks import EventHooks

async def get_token_by_value(db: AsyncSession, token_value: str):
    """Look up a token by its value.

    Parameters
    ----------
    db : AsyncSession
        An asynchronous database session
    token_value : str
        The value of the token

    Returns
    -------
    Token | None
        The token model, or None if it does not exist
    """
    stmt = select(models.ReportToken) \
        .where(models.ReportToken.value == token_value) \
        .options(selectinload(models.ReportToken.report))
    result = await db.execute(stmt)
    return result.scalar_one_or_none()

async def create_token(db: AsyncSession, token: schemas.ReportTokenCreateParams):
    """Create a new token.

    Parameters
    ----------
    db : AsyncSession
        An asynchronous database session
    token : schemas.ReportTokenCreateParams
        Payload

    Returns
    -------
    Token
        The token model

    Raises
    ------
    ValueError
        The token would already be expired
    NotFoundError
        No admin with the given ID exists
    AlreadyExistsError
        The admin's community differs from the given community ID
    """
    if token.expires_at < datetime.now(tz=timezone.utc):
        raise ValueError("Token would already be expired")
    
    admin = await db.get(models.Admin, token.admin_id)
    if not admin:
        raise NotFoundError("No admin with ID %s" % token.admin_id)
    if admin.community_id != token.community_id:
        raise AlreadyExistsError("Admin belongs to community with ID %s, not %s" % (admin.community_id, token.community_id))

    db_token = models.ReportToken(
        **token.model_dump(),
        value=secrets.token_urlsafe(16),
    )
    db.add(db_token)
    await db.flush()
    await db.refresh(db_token)
    return db_token



async def get_all_reports(db: AsyncSession, load_relations: bool = False, limit: int = 100, offset: int = 0):
    """Retrieve all reports.

    Parameters
    ----------
    db : AsyncSession
        An asynchronous database session
    load_relations : bool, optional
        Whether to also load relational properties, by default False
    limit : int, optional
        The amount of results to return, by default 100
    offset : int, optional
        Offset where from to start returning results, by default 0

    Returns
    -------
    List[Report]
        A sequence of all reports
    """
    if load_relations:
        options = (selectinload("*"),)
    else:
        options = (selectinload(models.Report.players),)

    stmt = select(models.Report).limit(limit).offset(offset).options(*options)
    result = await db.scalars(stmt)
    return result.all()


async def get_report_by_id(db: AsyncSession, report_id: int, load_relations: bool = False):
    """Look up a report by its ID.

    Parameters
    ----------
    db : AsyncSession
        An asynchronous database session
    report_id : int
        The ID of the report
    load_relations : bool, optional
        Whether to also load relational properties, by default False

    Returns
    -------
    Report | None
        The report model, or None if it does not exist
    """
    if load_relations:
        options = (selectinload(models.Report.players), selectinload(models.Report.messages), selectinload(models.Report.token),)
    else:
        options = (selectinload(models.Report.players),)

    return await db.get(models.Report, report_id, options=options)

async def get_reports_for_player(db: AsyncSession, player_id: str, load_relations: bool = False):
    """Get all reports of a player

    Parameters
    ----------
    db : AsyncSession
        An asynchronous database session
    player_id : str
        The ID of the player
    load_relations : bool, optional
        Whether to also load relational properties, by default False

    Returns
    -------
    List[Report]
        A sequence of report models
    """
    if load_relations:
        options = (selectinload("*"),)
    else:
        options = (selectinload(models.Report.players),)
    
    stmt = select(models.Report) \
        .join(models.Report.players) \
        .where(models.PlayerReport.player_id == player_id) \
        .options(*options)
    result = await db.scalars(stmt)
    return result.all()

async def create_report(db: AsyncSession, report: schemas.ReportCreateParams):
    """Create a new report.

    Parameters
    ----------
    db : AsyncSession
        An asynchronous database session
    report : schemas.ReportCreateParams
        Payload

    Returns
    -------
    Report
        The report model
    """
    report_payload = report.model_dump(exclude={"token", "players"})
    report_payload.update({
        "id": report.token.id,
        # If we don't initialize messages here it won't be fetched later, no clue why though
        "messages": [],
    })

    db_players = []
    for player in report.players:
        # This flushes, and since we don't want a partially initialized report
        # flushed, we do this first.
        db_player, _ = await get_or_create_player(db, schemas.PlayerCreateParams(
            id=player.player_id,
            bm_rcon_url=player.bm_rcon_url
        ))
        db_players.append(db_player)
        # player.bm_rcon_url = db_player.bm_rcon_url

    db_report = models.Report(**report_payload)
    for db_player in db_players:
        models.PlayerReport(
            report=db_report,
            player=db_player,
            player_name=player.player_name,
        )

    db.add(db_report)

    embed = await get_report_embed(report)
    channel = get_report_channel()
    message = await channel.send(embed=embed)
    db_report.message_id = message.id

    await db.flush()

    # For some reason this does not load db_report.messages
    db_report = await get_report_by_id(db, db_report.id, load_relations=True)

    EventHooks.invoke_report_create(schemas.ReportWithRelations.model_validate(db_report))

    return db_report

async def get_player(db: AsyncSession, player_id: str):
    """Look up a player.

    Parameters
    ----------
    db : AsyncSession
        An asynchronous database session
    player_id : str
        The ID of the player

    Returns
    -------
    Player | None
        The player model, or None if it does not exist
    """
    return await db.get(models.Player, player_id)

async def get_or_create_player(db: AsyncSession, player: schemas.PlayerCreateParams):
    """Look up a player, and create if it does not exist.

    Parameters
    ----------
    db : AsyncSession
        An asynchronous database session
    player : schemas.PlayerCreateParams
        Payload

    Returns
    -------
    tuple[Player, bool]
        The player model and a boolean indicating whether it was created or not
    """
    db_player = await get_player(db, player.id)
    created = False
    if db_player:
        if player.bm_rcon_url and player.bm_rcon_url != db_player.bm_rcon_url:
            db_player.bm_rcon_url = player.bm_rcon_url
            await db.flush()
    else:
        db_player = models.Player(**player.model_dump())
        db.add(db_player)
        await db.flush()
        created = True
    
    return db_player, created

def get_form_url(access_token: str):
    return REPORT_FORM_URL.format(access_token=access_token)

