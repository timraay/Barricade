import asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bunker import schemas
from bunker.constants import MAX_ADMIN_LIMIT
from bunker.db import models
from bunker.discord.audit import audit_community_admin_add, audit_community_admin_leave, audit_community_admin_remove, audit_community_change_owner, audit_community_created
from bunker.discord.communities import grant_admin_role, grant_owner_role, revoke_admin_roles
from bunker.exceptions import (
    AdminNotAssociatedError, AlreadyExistsError, AdminOwnsCommunityError,
    TooManyAdminsError, NotFoundError
)

async def get_all_admins(db: AsyncSession, load_relations: bool = False, limit: int = 100, offset: int = 0):
    """Retrieve all admins.

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
    List[Admin]
        A sequence of all admins
    """
    if load_relations:
        options = (selectinload("*"),)
    else:
        options = (selectinload(models.Admin.community), selectinload(models.Admin.owned_community))

    stmt = select(models.Admin).limit(limit).offset(offset).options(*options)
    result = await db.scalars(stmt)
    return result.all()

async def get_admin_by_id(db: AsyncSession, discord_id: int, load_relations: bool = False):
    """Look up an admin by their discord ID.

    Parameters
    ----------
    db : AsyncSession
        An asynchronous database session
    discord_id : int
        The discord ID of the admin
    load_relations : bool, optional
        Whether to also load relational properties, by default False

    Returns
    -------
    Admin | None
        The admin model, or None if it does not exist
    """
    if load_relations:
        options = (selectinload("*"),)
    else:
        options = (selectinload(models.Admin.community), selectinload(models.Admin.owned_community))

    return await db.get(models.Admin, discord_id, options=options)


async def get_all_communities(db: AsyncSession, load_relations: bool = False, limit: int = 100, offset: int = 0):
    """Retrieve all communities.

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
    List[Community]
        A sequence of all communities
    """
    if load_relations:
        options = (selectinload("*"),)
    else:
        options = (selectinload(models.Community.admins), selectinload(models.Community.owner), selectinload(models.Community.integrations))

    stmt = select(models.Community).limit(limit).offset(offset).options(*options)
    result = await db.scalars(stmt)
    return result.all()

async def get_community_by_id(db: AsyncSession, community_id: int, load_relations: bool = False):
    """Look up a community by its ID.

    Parameters
    ----------
    db : AsyncSession
        An asynchronous database session
    community_id : int
        The ID of the community
    load_relations : bool, optional
        Whether to also load relational properties, by default False

    Returns
    -------
    Community | None
        The community model, or None if it does not exist
    """
    if load_relations:
        options = (selectinload("*"),)
    else:
        options = (selectinload(models.Community.admins), selectinload(models.Community.owner), selectinload(models.Community.integrations))

    return await db.get(models.Community, community_id, options=options)
    
async def get_community_by_guild_id(db: AsyncSession, guild_id: int, load_relations: bool = False):
    """Look up a community by its forward Guild ID.

    Parameters
    ----------
    db : AsyncSession
        An asynchronous database session
    guild_id : int
        The ID of the forward guild
    load_relations : bool, optional
        Whether to also load relational properties, by default False

    Returns
    -------
    Community | None
        The community model, or None if it does not exist
    """
    if load_relations:
        options = (selectinload("*"),)
    else:
        options = (selectinload(models.Community.admins), selectinload(models.Community.owner), selectinload(models.Community.integrations))

    stmt = select(models.Community).where(
        models.Community.forward_guild_id == guild_id
    ).options(*options)
    return await db.scalar(stmt)

async def get_community_by_owner_id(db: AsyncSession, discord_id: int, load_relations: bool = False):
    """Look up the community an admin is owner of by their discord ID.

    Parameters
    ----------
    db : AsyncSession
        An asynchronous database session
    discord_id : int
        The discord ID of the admin
    load_relations : bool, optional
        Whether to also load relational properties, by default False

    Returns
    -------
    Community | None
        The Community model, or None if it does not exist
    """
    if load_relations:
        options = (selectinload("*"),)
    else:
        options = (selectinload(models.Community.admins), selectinload(models.Community.owner), selectinload(models.Community.integrations))

    stmt = select(models.Community).where(
        models.Community.owner_id == discord_id
    ).options(*options)
    return await db.scalar(stmt)


async def create_new_community(
        db: AsyncSession,
        community: schemas.CommunityCreateParams,
        by: str = None,
):
    """Create a new community.

    Parameters
    ----------
    db : AsyncSession
        An asyncronous database session
    community : schemas.CommunityCreate
        Payload

    Returns
    -------
    Community
        The community model

    Raises
    ------
    AlreadyExistsError
        The owner already belongs to a community
    """
    # Look if the owner exists already
    db_owner = await get_admin_by_id(db, community.owner_id)
    if not db_owner:
        # If no record exists, create new Admin record
        # Add the community_id later once the Community is created
        owner = schemas.AdminCreateParams(
            discord_id=community.owner_id,
            community_id=None,
            name=community.owner_name,
        )
        db_owner = await create_new_admin(db, owner)
    elif db_owner.community_id:
        # Owner is already part of a community
        raise AlreadyExistsError("Owner is already part of a community")
    elif db_owner.name != community.owner_name:
        # Update saved name of owner
        db_owner.name = community.owner_name
    
    # Create the Community
    db_community = models.Community(
        **community.model_dump(exclude={"owner_name", "owner_id"}),
        owner=db_owner,
    )
    db.add(db_community)
    # Flush and refresh to fetch the community's ID
    await db.flush()
    await db.refresh(db_community)

    # Update the owner's community
    db_owner.community_id = db_community.id
    await db.flush()
    await db_community.awaitable_attrs.owner

    # Grant role to the owner
    await grant_owner_role(db_owner.discord_id)

    asyncio.create_task(
        audit_community_created(
            community=db_community,
            by=by,
        )
    )

    return db_community

async def create_new_admin(
        db: AsyncSession,
        admin: schemas.AdminCreateParams,
        by: str = None,
):
    """Create a new admin.

    Parameters
    ----------
    db : AsyncSession
        An asynchronous database session
    admin : schemas.AdminCreate
        Payload

    Returns
    -------
    Admin
        The admin model

    Raises
    ------
    AlreadyExistsError
        This admin already exists
    TooManyAdminsError
        The community is not allowed any more admins
    NotFoundError
        No community with the given ID exists
    """
    if await get_admin_by_id(db, admin.discord_id):
        raise AlreadyExistsError
    
    if admin.community_id:
        db_community = await get_community_by_id(db, admin.community_id)
        if not db_community:
            raise NotFoundError("Community does not exist")
        elif len(db_community.admins) > MAX_ADMIN_LIMIT:
            # -1 to exclude owner, +1 to include the new admin
            raise TooManyAdminsError

    db_admin = models.Admin(**admin.model_dump())
    db.add(db_admin)
    await db.flush()
    await db.refresh(db_admin)

    if db_admin.community_id:
        await grant_admin_role(admin.discord_id)
        asyncio.create_task(
            audit_community_admin_add(db_community, db_admin, by=by)
        )

    return db_admin

async def admin_leave_community(
        db: AsyncSession,
        admin: models.Admin,
        by: str = None,
):
    """Remove an admin from a community.

    Parameters
    ----------
    db : AsyncSession
        An asynchronous database session
    admin : models.Admin
        The admin to remove

    Returns
    -------
    Admin
        The admin

    Raises
    ------
    NotFoundError
        The admin is not part of a community
    AdminOwnsCommunityError
        The admin is a community owner
    """
    if admin.community_id is None:
        raise NotFoundError
    
    community: models.Community = await admin.awaitable_attrs.community
    if community.owner_id == admin.discord_id:
        raise AdminOwnsCommunityError(admin)

    admin.community_id = None
    await db.flush()
    await db.refresh(admin)

    await revoke_admin_roles(admin.discord_id)

    asyncio.create_task(
        audit_community_admin_remove(community, admin, by=by)
    )

    return admin

async def admin_join_community(
        db: AsyncSession,
        admin: models.Admin,
        community: models.Community,
        by: str = None,
):
    """Add an admin to a community.

    Parameters
    ----------
    db : AsyncSession
        An asynchronous database session
    admin : models.Admin
        The admin to add
    community : models.Community
        The community to add the admin to

    Returns
    -------
    Admin
        The admin

    Raises
    ------
    AlreadyExistsError
        The admin is already part of a community
    TooManyAdminsError
        The community is not allowed any more admins
    """
    if admin.community_id:
        if admin.community_id == community.id:
            return admin
        else:
            raise AlreadyExistsError(admin)
        
    if len(await community.awaitable_attrs.admins) > MAX_ADMIN_LIMIT:
        # -1 to exclude owner, +1 to include the new admin
        raise TooManyAdminsError
        
    admin.community_id = community.id
    await db.flush()

    await grant_admin_role(admin.discord_id)

    await db.flush()
    await db.refresh(admin)

    asyncio.create_task(
        audit_community_admin_add(community, admin, by=by)
    )

    return admin

async def transfer_ownership(
        db: AsyncSession,
        community: models.Community,
        admin: models.Admin,
        by: str = None,
):
    """Transfer ownership of a community.

    Parameters
    ----------
    db : AsyncSession
        An asynchronous database session
    community : models.Community
        The community
    admin : models.Admin
        The admin to transfer ownership to

    Returns
    -------
    bool
        Whether something was changed

    Raises
    ------
    AdminNotAssociatedError
        The admin does not belong to the community
    """
    if community.owner_id == admin.discord_id:
        return False
    
    if admin.community_id != community.id:
        raise AdminNotAssociatedError(admin, community)
    
    old_owner: models.Admin = await community.awaitable_attrs.owner
    community.owner_id = admin.discord_id
    await db.flush()
    await db.refresh(community)
    await db.refresh(admin)

    await grant_admin_role(old_owner.discord_id)
    await grant_owner_role(community.owner_id)

    asyncio.create_task(
        audit_community_change_owner(old_owner, admin, by=by)
    )

    return True
