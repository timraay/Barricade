from sqlalchemy.ext.asyncio import AsyncSession

from barricade.crud.communities import get_admin_by_id, get_community_by_id
from barricade.db import models
from barricade.discord.utils import CustomException


async def get_community(db: AsyncSession, community_id: int) -> models.Community:
    db_community = await get_community_by_id(db, community_id)
    if not db_community:
        raise CustomException("Community not found")
    return db_community


async def get_admin(db: AsyncSession, admin_id: int) -> models.Admin:
    # Make sure the user is part of a community
    db_admin = await get_admin_by_id(db, admin_id)
    if not db_admin or not db_admin.community:
        raise CustomException("You need to be a community admin to do this!")
    return db_admin
