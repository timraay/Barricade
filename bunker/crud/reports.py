import asyncio
from datetime import datetime, timezone

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bunker import schemas
from bunker.crud.responses import get_response_stats
from bunker.db import models
from bunker.discord.audit import audit_report_deleted, audit_token_created
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

async def create_token(
        db: AsyncSession,
        token: schemas.ReportTokenCreateParams,
        by: str = None,
):
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
        **token.model_dump()
    )
    db.add(db_token)
    await db.flush()
    await db.refresh(db_token)

    asyncio.create_task(
        audit_token_created(db_token, by=by)
    )

    return db_token



async def get_all_reports(db: AsyncSession, load_token: bool = False, limit: int = 100, offset: int = 0):
    """Retrieve all reports.

    Parameters
    ----------
    db : AsyncSession
        An asynchronous database session
    load_token : bool, optional
        Whether to also load the relational token property, by default False
    limit : int, optional
        The amount of results to return, by default 100
    offset : int, optional
        Offset where from to start returning results, by default 0

    Returns
    -------
    List[Report]
        A sequence of all reports
    """
    if load_token:
        options = (selectinload(models.Report.players), selectinload(models.Report.token))
    else:
        options = (selectinload(models.Report.players),)

    stmt = select(models.Report).limit(limit).offset(offset).options(*options)
    result = await db.scalars(stmt)
    return result.all()


async def get_report_by_id(db: AsyncSession, report_id: int, load_token: bool = False, load_relations: bool = False):
    """Look up a report by its ID.

    Parameters
    ----------
    db : AsyncSession
        An asynchronous database session
    report_id : int
        The ID of the report
    load_token : bool, optional
        Whether to also load the token relational property, ignored if `load_relations`
        is True, by default False
    load_relations : bool, optional
        Whether to also load relational properties, by default False

    Returns
    -------
    Report | None
        The report model, or None if it does not exist
    """
    if load_relations:
        options = (selectinload("*"),)
    elif load_token:
        options = (selectinload(models.Report.players), selectinload(models.Report.token),)
    else:
        options = (selectinload(models.Report.players),)

    return await db.get(models.Report, report_id, options=options)

async def get_reports_for_player(db: AsyncSession, player_id: str, load_token: bool = False):
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
    if load_token:
        options = (selectinload(models.Report.players), selectinload(models.Report.token))
    else:
        options = (selectinload(models.Report.players),)
    
    stmt = select(models.Report) \
        .join(models.Report.players) \
        .where(models.PlayerReport.player_id == player_id) \
        .options(*options)
    result = await db.scalars(stmt)
    return result.all()

async def is_player_reported(db: AsyncSession, player_id: str):
    stmt = select(exists().where(models.PlayerReport.player_id == player_id))
    result = await db.scalar(stmt)
    return bool(result)

async def create_report(db: AsyncSession, report: schemas.ReportCreateParams):
    """Create a new report.

    This method will automatically commit after successfully creating
    a report!

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
    report_payload = report.model_dump(exclude={"token_id", "players"})
    report_payload.update({
        "id": report.token_id,
        "message_id": 0
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
    for player, db_player in zip(report.players, db_players):
        models.PlayerReport(
            report=db_report,
            player=db_player,
            player_name=player.player_name,
        )

    db.add(db_report)
    await db.flush()

    db_report = await get_report_by_id(db, db_report.id, load_token=True)
    if not db_report:
        raise RuntimeError("Report no longer exists")
    
    embed = await get_report_embed(db_report)
    channel = get_report_channel()
    message = await channel.send(embed=embed)
    db_report.message_id = message.id

    report_with_token = schemas.ReportWithToken.model_validate(db_report)
    await db.commit()
    EventHooks.invoke_report_create(report_with_token)

    return db_report

async def edit_report(db: AsyncSession, report: schemas.ReportCreateParams):
    db_report = await get_report_by_id(db, report.token_id, load_relations=True)
    if not db_report:
        raise NotFoundError("No report exists with ID %s" % report.token_id)
    
    old_report = schemas.ReportWithRelations.model_validate(db_report)
    
    # Index all existing PRs by their IDs
    db_prs = {
        db_pr.player_id: db_pr
        for db_pr in db_report.players
    }
    
    # Iterate over all submitted players
    for player in report.players:
        db_pr = db_prs.pop(player.player_id, None)
        if db_pr:
            # Player already existed, update their attributes and take them out
            # of the index.
            db_pr.player_name = player.player_name
            if player.bm_rcon_url:
                db_pr.player.bm_rcon_url = player.bm_rcon_url
        else:
            # Player did not yet exist, add to report
            db_player, _ = await get_or_create_player(db, schemas.PlayerCreateParams(
                id=player.player_id,
                bm_rcon_url=player.bm_rcon_url
            ))
            db_pr = models.PlayerReport(
                report=db_report,
                player=db_player,
                player_name=player.player_name,
            )
            # db_report.players.append(db_pr)
            db.add(db_pr)
    
    # Iterate over all remaining previous players and remove them
    for db_pr in db_prs.values():
        db_report.players.remove(db_pr)
        await db.delete(db_pr)

    db_report.body = report.body
    db_report.reasons_bitflag = report.reasons_bitflag
    db_report.reasons_custom = report.reasons_custom

    await db.flush()
    # await db.refresh(db_report)

    new_report = schemas.ReportWithRelations.model_validate(db_report)
    if (new_report != old_report):
        # Only invoke if something actually changed
        EventHooks.invoke_report_edit(new_report, old_report)

    return db_report

async def delete_report(db: AsyncSession, report_id: int, by: str = None):
    # Retrieve report
    db_report = await get_report_by_id(db, report_id, load_relations=True)
    if not db_report:
        raise NotFoundError("No report exists with ID %s" % report_id)
    
    # Retrieve stats for auditing
    stats = dict[int, schemas.ResponseStats]
    for pr in db_report.players:
        stats[pr.id] = await get_response_stats(db, pr)

    # Delete it
    await db.delete(db_report)
    await db.flush()

    # Invoke hooks and audit
    report = schemas.ReportWithRelations.model_validate(db_report)
    EventHooks.invoke_report_delete(report)
    asyncio.create_task(audit_report_deleted(report, stats, by=by))

    return True

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
