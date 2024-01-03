import discord
from discord import ButtonStyle

from bunker import schemas
from bunker.discord.utils import View

class PlayerReviewView(View):
    def __init__(self, responses: list[schemas.PendingResponse]):
        super().__init__(timeout=None)

        for row, response in enumerate(responses):
            if response.banned is None:
                self.add_item(discord.ui.Button(
                    label="Ban",
                    emoji="🔨",
                    style=ButtonStyle.green,
                    row=row,
                    custom_id=f"prr:{response.community.id}:{response.player_report.id}:1"
                ))
                self.add_item(discord.ui.Button(
                    label="Reject",
                    emoji="🚫",
                    style=ButtonStyle.red,
                    row=row,
                    custom_id=f"prr:{response.community.id}:{response.player_report.id}:0"
                ))
            self.add_item(discord.ui.Button(label=response.player_report.player_name, style=ButtonStyle.gray, row=row, disabled=True))
            if response.banned is True:
                self.add_item(discord.ui.Button(label="Player banned!", style=ButtonStyle.green, row=row, disabled=True))
            elif response.banned is False:
                self.add_item(discord.ui.Button(label="Report rejected!", style=ButtonStyle.red, row=row, disabled=True))
                
