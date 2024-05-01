import discord

from bunker import schemas
from bunker.discord.reports import get_report_embed
from bunker.discord.utils import View

class ReportManagementView(View):
    def __init__(self, report: schemas.ReportRef):
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(
            style=discord.ButtonStyle.blurple,
            label="Edit report",
            custom_id=f"rm:edit:{report.id}"
        ))
        self.add_item(discord.ui.Button(
            style=discord.ButtonStyle.red,
            label="Delete report",
            custom_id=f"rm:del:{report.id}"
        ))

    @staticmethod
    async def get_embed(
        report: schemas.ReportWithRelations,
        stats: dict[int, schemas.ResponseStats] = None
    ):
        embed = await get_report_embed(report, stats=stats, with_footer=False)
        embed.color = discord.Color.blurple()
        return embed
    
