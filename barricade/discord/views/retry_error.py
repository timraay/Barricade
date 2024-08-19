
from typing import Callable, Coroutine
from discord import ButtonStyle, Interaction
from barricade.discord.utils import CallableButton, View


class RetryErrorView(View):
    def __init__(self, callback: Callable[..., Coroutine], *args, **kwargs):
        super().__init__(timeout=60*60*24)

        self.callback = callback
        self.callback_args = args
        self.callback_kwargs = kwargs
        
        self.retry_button = CallableButton(self.retry, style=ButtonStyle.red, label="Retry")
        self.add_item(self.retry_button)

    async def retry(self, interaction: Interaction):
        await self.callback(*self.callback_args, **self.callback_kwargs)

        # Retry was successful so we delete the message
        await interaction.message.delete() # type: ignore
        await interaction.response.defer()
