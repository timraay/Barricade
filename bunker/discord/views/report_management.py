import discord

from bunker import schemas
from bunker.discord.utils import View

class ReportManagementView(View):
    def __init__(self, report: schemas.ReportRef):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            style=discord.ButtonStyle.red,
            label="Delete report",
            custom_id=f"rm:del:{report.id}"
        ))

    
