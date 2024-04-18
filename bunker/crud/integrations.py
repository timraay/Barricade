from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from bunker import schemas
from bunker.db import models
from bunker.exceptions import NotFoundError
from bunker.integrations.manager import IntegrationManager

async def create_integration_config(
        db: AsyncSession,
        params: schemas.IntegrationConfigParams,
):
    db_integration = models.Integration(
        **params.model_dump(),
        integration_type=params.integration_type # may be ClassVar
    )
    db.add(db_integration)

    # Get integration ID
    await db.flush()
    await db.refresh(db_integration)

    # Add integration to factory
    IntegrationManager().create(
        schemas.IntegrationConfig.model_validate(db_integration)
    )

    # Commit changes
    await db.commit()
    return db_integration


async def update_integration_config(
        db: AsyncSession,
        config: schemas.IntegrationConfig,
):
    stmt = update(models.Integration).values(
        **config.model_dump(),
        integration_type=config.integration_type # may be ClassVar
    ).where(
        models.Integration.id == config.id
    ).returning(models.Integration)
    db_integration = await db.scalar(stmt)

    if not db_integration:
        raise NotFoundError("Integration does not exist")

    IntegrationManager().load(config)

    await db.commit()
    return db_integration
