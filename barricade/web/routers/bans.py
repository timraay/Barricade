from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Security, status
from barricade import schemas
from barricade.bans import revoke_dangling_bans
from barricade.crud import bans
from barricade.db import DatabaseDep, models
from barricade.web.paginator import PaginatedResponse, PaginatorDep
from barricade.web import schemas as web_schemas
from barricade.web.routers.integrations import IntegrationDep, OwnIntegrationDep
from barricade.web.scopes import Scopes
from barricade.web.security import get_active_token, get_active_token_community

router = APIRouter(prefix="", tags=["Bans"])

def get_player_ban_dependency(load_relations: bool):
    async def inner(
        db: DatabaseDep,
        integration: IntegrationDep,
        player_id: str,
    ):
        assert integration.config.id is not None
        db_ban = await bans.get_ban_by_player_and_integration(
            db=db,
            player_id=player_id,
            integration_id=integration.config.id,
            load_relations=load_relations,
        )
        if db_ban is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Player is not banned"
            )
        return db_ban
    return inner
PlayerBanDep = Annotated[models.PlayerBan, Depends(get_player_ban_dependency(False))]
PlayerBanWithRelationsDep = Annotated[models.PlayerBan, Depends(get_player_ban_dependency(True))]


@router.get("/bans", response_model=PaginatedResponse[schemas.PlayerBanRef])
async def get_bans(
        db: DatabaseDep,
        paginator: PaginatorDep,
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token, scopes=(Scopes.BAN_READ).to_list())
        ],
        player_id: str | None = None,
        integration_id: int | None = None,
        community_id: int | None = None,
):
    result = await bans.get_all_bans(
        db=db,
        player_id=player_id,
        integration_id=integration_id,
        community_id=community_id,
        limit=paginator.limit,
        offset=paginator.offset,
    )
    return paginator.paginate(result)

@router.get("/bans/dangling", response_model=list[schemas.PlayerBanRef])
async def get_dangling_bans(
        db: DatabaseDep,
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token, scopes=Scopes.BAN_MANAGE.to_list())
        ],
        player_id: str | None = None,
        community_id: int | None = None,
):
    result = await bans.get_player_bans_without_responses(
        db,
        player_ids=[player_id] if player_id is not None else None,
        community_id=community_id,
    )
    return result

@router.delete("/bans/dangling")
async def delete_dangling_bans(
        db: DatabaseDep,
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token, scopes=Scopes.BAN_MANAGE.to_list())
        ],
        player_id: str | None = None,
        community_id: int | None = None,
) -> int:
    return await revoke_dangling_bans(
        db,
        player_ids=[player_id] if player_id is not None else None,
        community_id=community_id,
    )

@router.delete("/bans/{player_id}")
async def delete_ban_for_player(
        db: DatabaseDep,
        integration: IntegrationDep,
        ban: PlayerBanDep,
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token, scopes=Scopes.BAN_MANAGE.to_list())
        ]
) -> Literal[True]:
    await integration.unban_player(ban.player_id)
    await bans.expire_bans_of_player(db, ban.player_id, integration.config.community_id)
    return True


@router.get("/communities/me/bans", response_model=PaginatedResponse[schemas.PlayerBanRef])
async def get_own_community_bans(
        db: DatabaseDep,
        paginator: PaginatorDep,
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token_community(True), scopes=Scopes.BAN_ME_READ.to_list())
        ],
        player_id: str | None = None,
        integration_id: int | None = None,
):
    return get_bans(
        db, paginator, token,
        player_id=player_id,
        integration_id=integration_id,
        community_id=token.community_id,
    )

@router.delete("/communities/me/bans/{player_id}")
async def delete_own_community_ban_for_player(
        db: DatabaseDep,
        integration: OwnIntegrationDep,
        ban: PlayerBanDep,
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token_community(True), scopes=Scopes.BAN_ME_MANAGE.to_list())
        ]
) -> Literal[True]:
    return await delete_ban_for_player(db, integration, ban, token)

def setup(app: FastAPI):
    app.include_router(router)
