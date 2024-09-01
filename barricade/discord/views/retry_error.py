
from typing import Callable, Coroutine
from discord import ButtonStyle, Interaction
from barricade import schemas
from barricade.crud.communities import get_community_by_guild_id
from barricade.db import session_factory
from barricade.discord.communities import assert_has_admin_role
from barricade.discord.utils import CallableButton, View


class RetryErrorView(View):
    def __init__(self, callback: Callable[..., Coroutine], *args, **kwargs):
        super().__init__(timeout=60*60*24)

        self.callback = callback
        self.callback_args = args
        self.callback_kwargs = kwargs
        
        self.retry_button = CallableButton(self.retry, style=ButtonStyle.red, label="Retry")
        self.add_item(self.retry_button)

        self.add_item(CallableButton(self.dismiss, style=ButtonStyle.blurple, label="Dismiss"))

    async def verify_permissions(self, interaction: Interaction):
        # Make sure user has admin role
        async with session_factory() as db:
            assert interaction.guild_id is not None
            db_community = await get_community_by_guild_id(db, interaction.guild_id)
            community = schemas.CommunityRef.model_validate(db_community)
            await assert_has_admin_role(interaction.user, community) # type: ignore

    async def retry(self, interaction: Interaction):
        await self.verify_permissions(interaction)

        # Retry command
        await self.callback(*self.callback_args, **self.callback_kwargs)

        # Retry was successful so we delete the message
        await interaction.message.delete() # type: ignore
        await interaction.response.defer()

    async def dismiss(self, interaction: Interaction):
        await self.verify_permissions(interaction)
        assert interaction.message is not None
        await interaction.message.delete()
        await interaction.response.defer()
