from sqlalchemy.ext.asyncio import AsyncSession

from barricade import schemas
from barricade.crud.communities import get_community_by_id
from barricade.discord.utils import CustomException


async def get_community(db: AsyncSession, community_id: int) -> schemas.Community:
    db_community = await get_community_by_id(db, community_id)
    if not db_community:
        raise CustomException("Community not found")
    return schemas.Community.model_validate(db_community)
