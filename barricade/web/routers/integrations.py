from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Security, status
from barricade import schemas
from barricade.db import models
from barricade.integrations.integration import Integration
from barricade.integrations.manager import IntegrationManager
from barricade.web.paginator import PaginatedResponse, PaginatorDep
from barricade.web import schemas as web_schemas
from barricade.web.scopes import Scopes
from barricade.web.security import get_active_token, get_active_token_community

router = APIRouter(prefix="", tags=["Integrations"])

def get_integration_dependency(integration_id: int):
    im = IntegrationManager()
    result = im.get_by_id(integration_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration does not exist"
        )
    return result
IntegrationDep = Annotated[Integration, Depends(get_integration_dependency)]

def get_own_integration_dependency(
    integration: IntegrationDep,
    community: Annotated[
        models.Community,
        Security(get_active_token_community(False), scopes=Scopes.COMMUNITY_ME_READ.to_list())
    ],
):
    if integration.config.community_id != community.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integration does not exist"
        )
    return integration
OwnIntegrationDep = Annotated[Integration, Depends(get_own_integration_dependency)]

@router.get("/integrations", response_model=PaginatedResponse[schemas.SafeIntegrationConfig])
async def get_all_integrations(
        paginator: PaginatorDep,
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token, scopes=Scopes.COMMUNITY_READ.to_list())
        ]
):
    im = IntegrationManager()
    result = [im.config for im in im.get_all()]
    return paginator.paginate(result)

@router.get("/integrations/{integration_id}", response_model=schemas.SafeIntegrationConfig)
async def get_integration(
        integration: IntegrationDep,
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token, scopes=Scopes.COMMUNITY_READ.to_list())
        ]
):
    return integration.config

@router.post("/integrations/{integration_id}/enable", response_model=schemas.SafeIntegrationConfig)
async def enable_integration(
        integration: IntegrationDep,
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token, scopes=Scopes.COMMUNITY_MANAGE.to_list())
        ]
):
    if integration.config.enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Integration is already enabled"
        )

    await integration.enable()
    return integration.config

@router.post("/integrations/{integration_id}/disable", response_model=schemas.SafeIntegrationConfig)
async def disable_integration(
        integration: IntegrationDep,
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token, scopes=Scopes.COMMUNITY_MANAGE.to_list())
        ]
):
    if not integration.config.enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Integration is already disabled"
        )

    await integration.disable()
    return integration.config


@router.get("/communities/me/integrations/{integration_id}", response_model=schemas.SafeIntegrationConfig)
async def get_own_community_integration(
        integration: OwnIntegrationDep,
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token_community(False), scopes=Scopes.COMMUNITY_ME_READ.to_list())
        ],
):
    return integration.config

@router.post("/communities/me/integrations/{integration_id}/enable", response_model=schemas.SafeIntegrationConfig)
async def enable_own_community_integration(
        integration: OwnIntegrationDep,
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token_community(True), scopes=Scopes.COMMUNITY_ME_MANAGE.to_list())
        ],
):
    return enable_integration(integration, token)

@router.post("/communities/me/integrations/{integration_id}/disable", response_model=schemas.SafeIntegrationConfig)
async def disable_own_community_integration(
        integration: OwnIntegrationDep,
        token: Annotated[
            web_schemas.TokenWithHash,
            Security(get_active_token_community(True), scopes=Scopes.COMMUNITY_ME_MANAGE.to_list())
        ],
):
    return disable_integration(integration, token)

def setup(app: FastAPI):
    app.include_router(router)
