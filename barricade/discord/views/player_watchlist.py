import re
from sqlalchemy.ext.asyncio import AsyncSession

import discord
from discord import ui, ButtonStyle, Interaction

from barricade import schemas
from barricade.crud.communities import get_community_by_id
from barricade.crud.watchlists import create_watchlist, get_watchlist_by_player_and_community
from barricade.db import session_factory
from barricade.discord.communities import assert_has_admin_role
from barricade.discord.utils import CustomException, View, handle_error_wrap
from barricade.exceptions import AlreadyExistsError

class PlayerToggleWatchlistButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"watchlist:(?P<community_id>\d+):(?P<player_id>.+):(?P<is_watchlisted>0|1)"
):
    def __init__(
        self,
        button: discord.ui.Button,
        community_id: int,
        player_id: str,
        is_watchlisted: bool,
    ):
        self.community_id = community_id
        self.player_id = player_id
        self.is_watchlisted = is_watchlisted

        button.custom_id = f"watchlist:{self.community_id}:{self.player_id}:{int(self.is_watchlisted)}"
        
        super().__init__(button)
    
    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: discord.ui.Button, match: re.Match[str], /): # type: ignore
        return cls(
            button=item,
            community_id=int(match["community_id"]),
            player_id=match["player_id"],
            is_watchlisted=match["is_watchlisted"] == "1",
        )
    
    @classmethod
    def create(cls, community_id: int, player_id: str, is_watchlisted: bool, row: int | None = None):
        button = discord.ui.Button(
            label="Remove from watchlist" if is_watchlisted else "Add to watchlist",
            emoji="👁️",
            style=ButtonStyle.blurple if is_watchlisted else ButtonStyle.gray,
            row=row,
        )
        return cls(
            button=button,
            community_id=community_id,
            player_id=player_id,
            is_watchlisted=is_watchlisted,
        )
    
    @handle_error_wrap
    async def callback(self, interaction: Interaction):
        async with session_factory.begin() as db:
            db_community = await get_community_by_id(db, self.community_id)
            if not db_community:
                raise CustomException("Community not found")
            
            await assert_has_admin_role(
                interaction.user, # type: ignore
                schemas.CommunityRef.model_validate(db_community),
            )

            assert interaction.message is not None

            if self.is_watchlisted:
                await self.remove_watchlist(db)
            else:
                await self.add_watchlist(db)
            
            # Create copy of button
            new_button = self.create(
                community_id=self.community_id,
                player_id=self.player_id,
                is_watchlisted=not self.is_watchlisted,
                row=self.item.row,
            )

            # Replace button in view
            view = View.from_message(interaction.message)
            for item in view.children:
                if isinstance(item, ui.Button) and item.custom_id == self.custom_id:
                    view.remove_item(item)
                    view.add_item(new_button)
                    break
            else:
                raise RuntimeError("Expected to find button with custom ID %s" % self.custom_id)

            # Edit message
            await interaction.response.edit_message(view=view)

    async def add_watchlist(self, db: AsyncSession):
        params = schemas.PlayerWatchlistCreateParams(
            player_id=self.player_id,
            community_id=self.community_id,
        )
        try:
            await create_watchlist(db, params)
        except AlreadyExistsError:
            pass

    async def remove_watchlist(self, db: AsyncSession):
        db_watchlist = await get_watchlist_by_player_and_community(db, self.player_id, self.community_id)
        if db_watchlist:
            await db.delete(db_watchlist)
            await db.flush()