
from typing import Coroutine
from discord import ButtonStyle, Interaction
from bunker import schemas
from bunker.discord.communities import get_forward_channel
from bunker.discord.utils import CallableButton, View, get_error_embed


class RetryErrorView(View):
    def __init__(self, callback: Coroutine, *args, **kwargs):
        super().__init__(timeout=60*60*24)

        self.callback = callback
        self.callback_args = args
        self.callback_kwargs = kwargs
        
        self.retry_button = CallableButton(self.retry, style=ButtonStyle.red, label="Retry")
        self.add_item(self.retry_button)

    async def retry(self, interaction: Interaction):
        await self.callback(*self.callback_args, **self.callback_kwargs)

        # Retry was successful so we delete the message
        await interaction.delete_original_response(view=self)
        await interaction.response.defer()
