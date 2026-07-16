import logging
from datetime import UTC, datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Load, selectinload

from barricade import schemas
from barricade.crud.communities import get_admin_by_id
from barricade.crud.responses import get_response_stats
from barricade.db import models
from barricade.discord.audit import (
    AuditBy,
    audit_report_create,
    audit_report_delete,
    audit_report_edit,
    audit_token_create,
)
from barricade.discord.reports import get_report_channel
from barricade.enums import Game
from barricade.exceptions import AlreadyExistsError, NotFoundError
from barricade.hooks import EventHooks
from barricade.utils import safe_create_task


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
    stmt = (
        select(models.ReportToken)
        .where(models.ReportToken.value == token_value)
        .options(selectinload(models.ReportToken.report))
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def create_token(
    db: AsyncSession,
    params: schemas.ReportTokenCreateParams,
    by: AuditBy | None = None,
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
    if params.expires_at < datetime.now(tz=UTC):
        raise ValueError("Token would already be expired")

    admin = await get_admin_by_id(db, params.admin_id)
    if not admin:
        raise NotFoundError(f"No admin with ID {params.admin_id}")
    if not admin.community:
        raise NotFoundError(
            f"Admin with ID {params.admin_id} is not part of any community"
        )
    if admin.community_id != params.community_id:
        raise AlreadyExistsError(
            f"Admin belongs to community with ID {admin.community_id}, not {params.community_id}"
        )

    db_token = models.ReportToken(**params.model_dump())
    db.add(db_token)
    await db.flush()
    await db.refresh(db_token)

    token = schemas.ReportTokenRef.model_validate(db_token)

    safe_create_task(audit_token_create(token, by=by))

    return db_token


async def get_all_reports(
    db: AsyncSession,
    community_id: int | None = None,
    load_token: bool = False,
    limit: int = 100,
    offset: int = 0,
    created_before: datetime | None = None,
    created_after: datetime | None = None,
    edited_before: datetime | None = None,
    edited_after: datetime | None = None,
    game: Game | None = None,
):
    """Retrieve all reports.

    Parameters
    ----------
    db : AsyncSession
        An asynchronous database session
    community_id : int, optional
        An ID of a community to filter reports by
    load_token : bool, optional
        Whether to also load the relational token property, by default False
    limit : int, optional
        The amount of results to return, by default 100
    offset : int, optional
        Offset where from to start returning results, by default 0
    created_before : datetime, optional
        Filter for reports created before this datetime, by default None
    created_after : datetime, optional
        Filter for reports created after this datetime, by default None
    edited_before : datetime, optional
        Filter for reports last edited before this datetime, by default None
    edited_after : datetime, optional
        Filter for reports last edited after this datetime, by default None
    game : Game, optional
        Filter for reports of a specific game, by default None

    Returns
    -------
    List[Report]
        A sequence of all reports
    """
    if load_token:
        options = (
            selectinload(models.Report.players),
            selectinload(models.Report.token),
        )
    else:
        options = (selectinload(models.Report.players),)

    stmt = select(models.Report).limit(limit).offset(offset).options(*options)

    if community_id is not None:
        stmt = stmt.join(models.Report.token).where(
            models.ReportToken.community_id == community_id
        )

    if created_before is not None:
        stmt = stmt.where(models.Report.created_at <= created_before)
    if created_after is not None:
        stmt = stmt.where(models.Report.created_at >= created_after)

    if edited_before is not None:
        stmt = stmt.where(
            or_(
                models.Report.edited_at <= edited_before,
                models.Report.edited_at.is_(None),
            )
        )
    if edited_after is not None:
        stmt = stmt.where(models.Report.edited_at >= edited_after)

    if game is not None:
        stmt = stmt.where(models.Report.game == game)

    result = await db.scalars(stmt)
    return result.all()


async def get_report_by_id(
    db: AsyncSession,
    report_id: int,
    load_token: bool = False,
    load_relations: bool = False,
):
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
        options = (Load(models.Report).selectinload("*"),)
    elif load_token:
        options = (
            selectinload(models.Report.players),
            selectinload(models.Report.token),
        )
    else:
        options = (selectinload(models.Report.players),)

    return await db.get(models.Report, report_id, options=options)


async def get_reports_for_player(
    db: AsyncSession, player_id: str, load_token: bool = False
):
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
        options = (
            selectinload(models.Report.players),
            selectinload(models.Report.token),
        )
    else:
        options = (selectinload(models.Report.players),)

    stmt = (
        select(models.Report)
        .join(models.Report.players)
        .where(models.PlayerReport.player_id == player_id)
        .options(*options)
    )
    result = await db.scalars(stmt)
    return result.all()


async def is_player_reported(
    db: AsyncSession,
    player_id: str,
    game: Game | None = None,
):
    stmt = (
        select(1)
        .select_from(models.PlayerReport)
        .where(
            models.PlayerReport.player_id == player_id,
        )
    )

    if game is not None:
        stmt = stmt.join(models.PlayerReport.report).where(models.Report.game == game)

    stmt = select(stmt.exists())
    result = await db.scalar(stmt)
    return bool(result)


async def create_report(
    db: AsyncSession,
    params: schemas.ReportCreateParams,
    by: AuditBy | None = None,
):
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
    report_payload = params.model_dump(exclude={"token_id", "players"})
    report_payload.update({"id": params.token_id, "message_id": 0})

    db_players = []
    for player in params.players:
        # This flushes, and since we don't want a partially initialized report
        # flushed, we do this first.
        db_player, _ = await get_or_create_player(
            db,
            schemas.PlayerCreateParams(
                id=player.player_id,
                bm_rcon_url=player.bm_rcon_url,
                hll_eos_id=player.hll_eos_id,
                hllv_eos_id=player.hllv_eos_id,
                platform=player.platform,
            ),
        )
        db_players.append(db_player)
        # player.bm_rcon_url = db_player.bm_rcon_url

    db_report = models.Report(**report_payload)
    for player, db_player in zip(params.players, db_players, strict=True):
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

    report = schemas.ReportWithToken.model_validate(db_report)

    from barricade.discord.views.report_public_review import (
        get_report_public_review_view,
    )

    view = await get_report_public_review_view(report)
    channel = get_report_channel(report.game)
    message = await channel.send(view=view)
    db_report.message_id = message.id

    await db.commit()
    EventHooks.invoke_report_create(report)
    safe_create_task(audit_report_create(report, by=by))

    return db_report


async def edit_report(
    db: AsyncSession,
    report: schemas.ReportCreateParams,
    by: AuditBy | None = None,
):
    db_report = await get_report_by_id(db, report.token_id, load_relations=True)
    if not db_report:
        raise NotFoundError(f"No report exists with ID {report.token_id}")

    old_report = schemas.ReportWithRelations.model_validate(db_report)

    # Index all existing PRs by their IDs
    db_prs = {db_pr.player_id: db_pr for db_pr in db_report.players}

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
            db_player, _ = await get_or_create_player(
                db,
                schemas.PlayerCreateParams(
                    id=player.player_id,
                    bm_rcon_url=player.bm_rcon_url,
                    hll_eos_id=player.hll_eos_id,
                    hllv_eos_id=player.hllv_eos_id,
                    platform=player.platform,
                ),
            )
            db_pr = models.PlayerReport(
                # report=db_report,
                player=db_player,
                player_name=player.player_name,
            )
            db_report.players.append(db_pr)
            # db.add(db_pr)

    # Iterate over all remaining previous players and remove them
    for db_pr in db_prs.values():
        db_report.players.remove(db_pr)
        # await db.delete(db_pr)

    db_report.body = report.body
    db_report.reasons_bitflag = report.reasons_bitflag
    db_report.reasons_custom = report.reasons_custom
    db_report.attachment_urls = report.attachment_urls
    db_report.game = report.game
    db_report.platforms_bitflag = report.platforms_bitflag
    db_report.effective_platforms_bitflag = report.effective_platforms_bitflag
    db_report.edited_at = db_report.edited_at
    db_report.edited_by = db_report.edited_by

    await db.flush()
    # await db.refresh(db_report)

    new_report = schemas.ReportWithRelations.model_validate(db_report)
    if new_report != old_report:
        # Only invoke if something actually changed
        EventHooks.invoke_report_edit(new_report, old_report)
        safe_create_task(audit_report_edit(new_report, by=by))

    return db_report


async def delete_report(
    db: AsyncSession,
    report_id: int,
    by: AuditBy | None = None,
):
    """Delete a report.

    This method will automatically commit after successfully creating
    a report!

    Parameters
    ----------
    db : AsyncSession
        An asynchronous database session
    report_id : int
        The ID of the report to delete

    Raises
    ------
    NotFoundError
        No report exists with the given ID
    """
    # Retrieve report
    db_report = await get_report_by_id(db, report_id, load_relations=True)
    if not db_report:
        raise NotFoundError(f"No report exists with ID {report_id}")

    # Retrieve stats for auditing
    stats: dict[int, schemas.ResponseStats] = {}
    for db_pr in db_report.players:
        pr = schemas.PlayerReportRef.model_validate(db_pr)
        stats[pr.id] = await get_response_stats(db, pr)

    # Delete it
    await db.delete(db_report)
    await db.commit()

    # Invoke hooks and audit
    report = schemas.ReportWithRelations.model_validate(db_report)
    EventHooks.invoke_report_delete(report)
    safe_create_task(audit_report_delete(report, stats, by=by))

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
        dirty = False
        for attr in ("bm_rcon_url", "hll_eos_id", "hllv_eos_id", "platform"):
            new_value = getattr(player, attr)
            old_value = getattr(db_player, attr)
            if new_value and new_value != old_value:
                if old_value:
                    logging.warning(
                        "Updating %s for player %s from %s to %s",
                        attr,
                        player.id,
                        old_value,
                        new_value,
                    )
                setattr(db_player, attr, new_value)
                dirty = True
        if dirty:
            await db.flush()
    else:
        db_player = models.Player(**player.model_dump())
        db.add(db_player)
        await db.flush()
        created = True

    return db_player, created


async def get_report_message_by_community_id(
    db: AsyncSession, report_id: int, community_id: int | None
):
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
    stmt = select(models.ReportMessage).where(
        models.ReportMessage.report_id == report_id,
        models.ReportMessage.community_id == community_id,
    )
    return await db.scalar(stmt)
