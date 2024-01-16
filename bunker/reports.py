import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bunker import schemas
from bunker.constants import REPORT_FORM_URL
from bunker.db import models, session_factory
from bunker.discord import bot
from bunker.hooks import EventHooks, add_hook

async def get_token_data(db: AsyncSession, access_token: str, load_relations: bool = False):
    stmt = select(models.ReportToken).where(models.ReportToken.token == access_token).limit(1)
    if load_relations:
        stmt = stmt.options(selectinload("*"))
    result = await db.execute(stmt)
    return result.scalar_one_or_none()

async def create_token(db: AsyncSession, token: schemas.TokenCreateParams):
    db_token = models.ReportToken(
        **token.model_dump(),
        token=secrets.token_urlsafe(16),
    )
    db.add(db_token)
    await db.commit()
    await db.refresh(db_token)
    return db_token

async def get_report_by_id(db: AsyncSession, report_id: int):
    return await db.get(models.Report, report_id)

async def create_report(db: AsyncSession, report: schemas.ReportCreateParams):
    db_reported_players = []
    for player in report.players:
        db_player, _ = await get_or_create_player(db, player)
        db_reported_player = models.PlayerReport(
            player=db_player,
            player_name=player.name,
        )
        db_reported_players.append(db_reported_player)
        player.bm_rcon_url = db_player.bm_rcon_url

    db_report = models.Report(
        id=report.token.id,
        timestamp=report.timestamp,
        body=report.body,
        players=db_reported_players,
        reasons=[models.ReportReason(reason=reason) for reason in report.reasons],
        attachments=[models.ReportAttachment(url=attachment) for attachment in report.attachment_urls],
    )
    db.add(db_report)

    embed = await bot.get_report_embed(report)
    message = await bot.send_report(embed)
    db_report.message_id = message.id

    await db.commit()
    await db.refresh(db_report)

    EventHooks.invoke_report_create(db_report, report)

    return db_report

@add_hook(EventHooks.report_create)
async def forward_report_to_communities(report: schemas.Report, params: schemas.ReportCreateParams):
    embed = None
    async with session_factory() as db:
        stmt = select(models.Community).where(
            models.Community.forward_guild_id.is_not(None),
            models.Community.forward_channel_id.is_not(None),
        )
        result = await db.scalars(stmt)
        communities = result.all()

        for community in communities:
            if embed is None:
                embed = await bot.get_report_embed(params)
            await bot.forward_report_to_community(report, community, embed)

async def get_player(db: AsyncSession, player_id: str):
    return await db.get(models.Player, player_id)

async def get_or_create_player(db: AsyncSession, player: schemas.PlayerCreateParams):
    db_player = await get_player(db, player.id)
    created = False
    if db_player:
        if player.bm_rcon_url and player.bm_rcon_url != db_player.bm_rcon_url:
            db_player.bm_rcon_url = player.bm_rcon_url
            await db.commit()
    else:
        db_player = models.Player(
            id=player.id,
            bm_rcon_url=player.bm_rcon_url,
        )
        db.add(db_player)
        await db.commit()
        created = True
    
    return db_player, created

def get_form_url(access_token: str):
    return REPORT_FORM_URL.format(access_token=access_token)

