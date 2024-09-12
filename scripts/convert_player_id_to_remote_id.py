import asyncio
from sqlalchemy import select
from barricade import schemas
from barricade.db import models, session_factory
from barricade.enums import IntegrationType
from barricade.integrations.crcon import CRCONIntegration
from barricade.logger import get_logger

async def main():
    """Script to correct an issue where the player IDs were used as remote ban IDs
    for CRCON integrations."""
    async with session_factory.begin() as db:
        stmt = select(models.Integration).where(
            models.Integration.integration_type == IntegrationType.COMMUNITY_RCON
        )
        results = await db.scalars(stmt)
        for db_config in results:
            logger = get_logger(db_config.community_id)
            try:
                config = schemas.CRCONIntegrationConfig.model_validate(db_config)
                integration = CRCONIntegration(config)
            except:
                logger.exception("Failed to load integration %r", db_config)
                continue

            # records = await integration.get_blacklist_bans()
            records = {
                "76560000000000001": {
                    "id": 3,
                    "player_id": "76560000000000001"
                },
                "76560000000000002": {
                    "id": 3,
                    "player_id": "76560000000000002"
                }
            }

            for record in records.values():
                player_id = record["player_id"]
                remote_id = str(record["id"])
                
                stmt = select(models.PlayerBan).where(
                    models.PlayerBan.integration_id == integration.config.id,
                    models.PlayerBan.remote_id == player_id,
                )
                db_ban = await db.scalar(stmt)
                if not db_ban:
                    logger.info("No local ban found for %s", player_id)
                    continue

                db_ban.remote_id = remote_id
                logger.info("Changed remote id %s to %s", player_id, remote_id)

if __name__ == '__main__':
    asyncio.run(main())