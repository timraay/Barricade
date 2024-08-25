import asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from barricade import schemas
from barricade.constants import MAX_ADMIN_LIMIT
from barricade.db import models
from barricade.discord.audit import audit_community_admin_add, audit_community_admin_remove, audit_community_change_owner, audit_community_create, audit_community_edit
from barricade.discord.communities import revoke_user_roles, update_user_roles
from barricade.exceptions import (
    AdminNotAssociatedError, AlreadyExistsError, AdminOwnsCommunityError,
    TooManyAdminsError, NotFoundError
)
from barricade.utils import safe_create_task

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

async def get_community_by_name(db: AsyncSession, name: str, load_relations: bool = False):
    """Look up a community by its name.

    Parameters
    ----------
    db : AsyncSession
        An asynchronous database session
    name : str
        The name of the community
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

    stmt = select(models.Community).where(models.Community.name == name).options(*options)
    return await db.scalar(stmt)
    
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
        params: schemas.CommunityCreateParams,
        by: str | None = None,
):
    """Create a new community.

    Parameters
    ----------
    db : AsyncSession
        An asyncronous database session
    params : schemas.CommunityCreateParams
        Payload

    Returns
    -------
    Community
        The community model

    Raises
    ------
    AlreadyExistsError
        A community with the same name already exists
    AlreadyExistsError
        The owner already belongs to a community
    """
    # Look if a community with the same name already exists
    if await get_community_by_name(db, params.name):
        raise AlreadyExistsError("Name is already in use")

    # Look if the owner exists already
    db_owner = await get_admin_by_id(db, params.owner_id)
    if not db_owner:
        # If no record exists, create new Admin record
        # Add the community_id later once the Community is created
        owner = schemas.AdminCreateParams(
            discord_id=params.owner_id,
            community_id=None,
            name=params.owner_name,
        )
        db_owner = await create_new_admin(db, owner)
    elif db_owner.community_id:
        # Owner is already part of a community
        raise AlreadyExistsError("Owner is already part of a community")
    elif db_owner.name != params.owner_name:
        # Update saved name of owner
        db_owner.name = params.owner_name
    
    # Create the Community
    db_community = models.Community(
        **params.model_dump(exclude={"owner_name", "owner_id"}),
        owner=db_owner,
    )
    db.add(db_community)
    # Flush and refresh to fetch the community's ID
    await db.flush()
    await db.refresh(db_community)

    # Update the owner's community
    db_owner.community_id = db_community.id
    await db.flush()

    community = schemas.CommunityRef.model_validate(db_community)
    owner = schemas.AdminRef.model_validate(db_owner)

    # Grant role to the owner
    await update_user_roles(db_owner.discord_id, community=community)

    safe_create_task(
        audit_community_create(
            community=community,
            owner=owner,
            by=by,
        )
    )

    return db_community

async def edit_community(
        db: AsyncSession,
        db_community: models.Community,
        params: schemas.CommunityEditParams,
        by: str | None = None,
):
    """Edit an existing community.

    Parameters
    ----------
    db : AsyncSession
        An asyncronous database session
    db_community : models.Community
        The community to be edited
    params : schemas.CommunityEditParams
        Payload

    Returns
    -------
    Community
        The community model

    Raises
    ------
    AlreadyExistsError
        The updated name is already in use
    """
    # Look if a community with the same name already exists
    if other_community := await get_community_by_name(db, params.name):
        if other_community.id != db_community.id:
            raise AlreadyExistsError("Name is already in use")

    for key, val in params:
        setattr(db_community, key, val)

    await db.flush()
    
    safe_create_task(
        audit_community_edit(
            community=schemas.Community.model_validate(db_community),
            by=by,
        )
    )

    return db_community

async def create_new_admin(
        db: AsyncSession,
        params: schemas.AdminCreateParams,
        by: str | None = None,
):
    """Create a new admin.

    Parameters
    ----------
    db : AsyncSession
        An asynchronous database session
    params : schemas.AdminCreateParams
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
    if await get_admin_by_id(db, params.discord_id):
        raise AlreadyExistsError
    
    db_community = None
    if params.community_id:
        db_community = await get_community_by_id(db, params.community_id)
        if not db_community:
            raise NotFoundError("Community with ID %s does not exist" % params.community_id)
        elif len(db_community.admins) > MAX_ADMIN_LIMIT:
            # -1 to exclude owner, +1 to include the new admin
            raise TooManyAdminsError

    db_admin = models.Admin(**params.model_dump())
    db.add(db_admin)
    await db.flush()
    await db.refresh(db_admin)

    if db_community:
        community = schemas.CommunityRef.model_validate(db_community)
        admin = schemas.AdminRef.model_validate(db_admin)
        await update_user_roles(params.discord_id, community=community)
        safe_create_task(
            audit_community_admin_add(community, admin, by=by)
        )

    return db_admin

async def admin_leave_community(
        db: AsyncSession,
        db_admin: models.Admin,
        by: str | None = None,
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
    if db_admin.community_id is None:
        raise NotFoundError("Admin with ID %s is not part of a community" % db_admin.discord_id)
    
    db_community: models.Community = await db_admin.awaitable_attrs.community

    admin = schemas.Admin.model_validate(db_admin)
    community = schemas.CommunityRef.model_validate(db_community)
    
    if community.owner_id == admin.discord_id:
        raise AdminOwnsCommunityError(admin)
    

    db_admin.community_id = None
    await db.flush()
    await db.refresh(db_admin)

    await revoke_user_roles(admin.discord_id, strict=False)

    safe_create_task(
        audit_community_admin_remove(community, admin, by=by)
    )

    return db_admin

async def admin_join_community(
        db: AsyncSession,
        db_admin: models.Admin,
        db_community: models.Community,
        by: str | None = None,
):
    """Add an admin to a community.

    Parameters
    ----------
    db : AsyncSession
        An asynchronous database session
    db_admin : models.Admin
        The admin to add
    db_community : models.Community
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
    if db_admin.community_id:
        if db_admin.community_id == db_community.id:
            return db_admin
        else:
            raise AlreadyExistsError(db_admin)
        
    if len(await db_community.awaitable_attrs.admins) > MAX_ADMIN_LIMIT:
        # -1 to exclude owner, +1 to include the new admin
        raise TooManyAdminsError
        
    db_admin.community_id = db_community.id
    await db.flush()

    community = schemas.CommunityRef.model_validate(db_community)
    admin = schemas.AdminRef.model_validate(db_admin)

    await update_user_roles(db_admin.discord_id, community=community)

    await db.refresh(db_admin)

    safe_create_task(
        audit_community_admin_add(community, admin, by=by)
    )

    return db_admin

async def transfer_ownership(
        db: AsyncSession,
        community_id: int,
        admin_id: int,
        by: str | None = None,
):
    """Transfer ownership of a community.

    Parameters
    ----------
    db : AsyncSession
        An asynchronous database session
    community_id : int
        The ID of the community
    admin_id : int
        The ID of the admin to transfer ownership to

    Returns
    -------
    bool
        Whether something was changed

    Raises
    ------
    NotFoundError
        The community or admin are not found
    AdminNotAssociatedError
        The admin does not belong to the community
    """
    db_community = await get_community_by_id(db, community_id)
    if not db_community:
        raise NotFoundError("Community with ID %s does not exist" % community_id)
    community = schemas.Community.model_validate(db_community)

    db_admin = await get_admin_by_id(db, admin_id)
    if not db_admin:
        raise NotFoundError("Admin with ID %s does not exist" % admin_id)
    admin = schemas.Admin.model_validate(db_admin)

    if db_community.owner_id == db_admin.discord_id:
        return False
    
    if db_admin.community_id != db_community.id:
        raise AdminNotAssociatedError(admin, community)
    
    db_old_owner: models.Admin = await db_community.awaitable_attrs.owner
    old_owner = schemas.AdminRef.model_validate(db_old_owner)

    db_community.owner_id = db_admin.discord_id
    await db.flush()
    await db.refresh(db_community)
    await db.refresh(db_admin)

    community = schemas.Community.model_validate(db_community)
    await update_user_roles(db_community.owner_id, community=community)
    await update_user_roles(old_owner.discord_id, community=community, strict=False)

    safe_create_task(
        audit_community_change_owner(old_owner, admin, by=by)
    )

    return True
