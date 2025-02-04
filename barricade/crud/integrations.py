from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from barricade import schemas
from barricade.constants import MAX_INTEGRATION_LIMIT
from barricade.db import models
from barricade.exceptions import MaxLimitReachedError, NotFoundError

async def create_integration_config(
        db: AsyncSession,
        params: schemas.IntegrationConfigParams,
):
    stmt = (
        select(func.count("*")) # type: ignore
        .select_from(models.Integration)
        .where(models.Integration.community_id == params.community_id)
    )
    result = await db.execute(stmt)
    row = result.first()
    assert row is not None
    if row[0] >= MAX_INTEGRATION_LIMIT:
        raise MaxLimitReachedError(MAX_INTEGRATION_LIMIT)

    db_integration = models.Integration(
        **params.model_dump(exclude={"integration_type"}),
        integration_type=params.integration_type # may be ClassVar
    )
    db.add(db_integration)

    # Get integration ID
    await db.flush()
    await db.refresh(db_integration)

    return db_integration


async def update_integration_config(
        db: AsyncSession,
        config: schemas.IntegrationConfig,
):
    stmt = update(models.Integration).values(
        **config.model_dump(exclude={"integration_type"}, exclude_unset=True),
        integration_type=config.integration_type # may be ClassVar
    ).where(
        models.Integration.id == config.id
    ).returning(models.Integration)
    db_integration = await db.scalar(stmt)

    if not db_integration:
        raise NotFoundError("Integration does not exist")

    return db_integration

async def delete_integration_config(
        db: AsyncSession,
        config: schemas.IntegrationConfig,
):
    db_integration = await db.get(models.Integration, config.id)
    if not db_integration:
        raise NotFoundError("Integration does not exist")
    
    await db.delete(db_integration)
    await db.flush()
    return
