from enum import StrEnum
from typing import Annotated
import discord
from fastapi import APIRouter, Body, FastAPI, Security
from pydantic import BaseModel

from barricade import schemas
from barricade.crud.communities import get_all_communities
from barricade.db import DatabaseDep
from barricade.discord.communities import get_alerts_role_mention, get_confirmations_channel
from barricade.discord.utils import get_danger_embed, get_error_embed, get_neutral_embed, get_question_embed, get_success_embed
from barricade.enums import IntegrationType
from barricade.integrations.manager import IntegrationManager
from barricade.web.scopes import Scopes
from barricade.web.security import get_active_token

router = APIRouter(
    prefix="/admin-tools",
    tags=["Admin Tools"],
    dependencies=[Security(get_active_token, scopes=Scopes.STAFF.to_list())])

class MessageType(StrEnum):
    NEUTRAL = "neutral"
    ERROR = "error"
    QUESTION = "question"
    DANGER = "danger"
    SUCCESS = "success"

    def to_embed(self, title: str, description: str | None = None) -> discord.Embed:
        match self:
            case MessageType.NEUTRAL:
                embed_fn = get_neutral_embed
            case MessageType.ERROR:
                embed_fn = get_error_embed
            case MessageType.QUESTION:
                embed_fn = get_question_embed
            case MessageType.DANGER:
                embed_fn = get_danger_embed
            case MessageType.SUCCESS:
                embed_fn = get_success_embed
            case _:
                embed_fn = get_neutral_embed
        
        return embed_fn(title=title, description=description)
    
class MessageFilters(BaseModel):
    has_integration_type: IntegrationType | None = None

    def apply(self, community: schemas.Community) -> bool:
        if self.has_integration_type is not None:
            im = IntegrationManager()
            for integration in im.get_all():
                if (
                    integration.config.community_id == community.id
                    and integration.config.integration_type == self.has_integration_type
                ):
                    break
            else:
                # Loop did not break; no integration was found
                return False
        
        return True

@router.post("/forward-message")
async def forward_message(
    db: DatabaseDep,
    title: Annotated[str, Body(max_length=256)],
    description: Annotated[str, Body(max_length=4096)],
    type: Annotated[MessageType, Body()] = MessageType.NEUTRAL,
    filters: Annotated[MessageFilters, Body()] = MessageFilters(),
    notify: Annotated[bool, Body()] = False,
) -> int:
    embed = type.to_embed(title, description)
    success_count = 0
    allowed_mentions = discord.AllowedMentions(roles=True) if notify else discord.AllowedMentions.none()

    db_communities = await get_all_communities(db)
    for db_community in db_communities:
        community = schemas.Community.model_validate(db_community)
        if filters.apply(community):
            channel = get_confirmations_channel(community)
            if channel:
                content = get_alerts_role_mention(community) if notify else None
                try:
                    await channel.send(
                        content=content,
                        embed=embed,
                        allowed_mentions=allowed_mentions,
                    )
                except Exception:
                    pass
                else:
                    success_count += 1
    
    return success_count


def setup(app: FastAPI):
    app.include_router(router)
