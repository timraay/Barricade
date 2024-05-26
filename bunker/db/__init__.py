from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncAttrs, AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from typing import Annotated

from bunker.constants import DB_URL

class ModelBase(AsyncAttrs, DeclarativeBase):
    pass

engine = create_async_engine(DB_URL)
"""Asynchronous database engine"""

session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
"""Factory method for creating asynchronous sessions::

    async with session_factory() as sess:
        await sess.execute(...)

FastAPI routes should use the get_db generator as a
dependency instead.
"""

# Dependency for FastAPI
async def get_db():
    """Database dependency for use in FastAPI. Use
    session_factory otherwise.

    Yields
    ------
    AsyncSession
        An asynchronous database session
    """
    async with session_factory.begin() as db:
        yield db
DatabaseDep = Annotated[AsyncSession, Depends(get_db)]

async def create_tables():
    """Create all tables if they do not exist
    yet.

    Note that this only creates tables that do not
    yet exist. Existing tables are never altered.
    """
    # Load all models
    import bunker.db.models
    # Create the tables
    async with engine.begin() as db:
        await db.run_sync(ModelBase.metadata.create_all)
