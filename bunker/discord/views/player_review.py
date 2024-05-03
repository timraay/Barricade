import discord
from discord import ButtonStyle

from bunker import schemas
from bunker.discord.utils import View
from bunker.discord.reports import get_report_embed
from bunker.enums import ReportRejectReason

class PlayerReviewView(View):
    def __init__(self, responses: list[schemas.PendingResponse]):
        super().__init__(timeout=None)

        for row, response in enumerate(responses):
            self.add_item(discord.ui.Button(
                label=f"# {row + 1}.",
                style=ButtonStyle.red if response.banned is True else ButtonStyle.gray,
                custom_id=f"prr:refresh:{response.community.id}:{response.player_report.report_id}",
                row=row
            ))

            if response.banned is True:
                self.add_item(discord.ui.Button(
                    label="Unban player",
                    style=ButtonStyle.blurple,
                    disabled=False,
                    custom_id=f"prr:unban:{response.community.id}:{response.player_report.id}",
                    row=row
                ))
            else:
                self.add_item(discord.ui.Button(
                    label="Ban player...",
                    style=ButtonStyle.red,
                    disabled=False,
                    custom_id=f"prr:ban:{response.community.id}:{response.player_report.id}",
                    row=row
                ))


            for reason in ReportRejectReason:
                if response.banned is None:
                    button_style = ButtonStyle.blurple
                if response.reject_reason == reason:
                    button_style = ButtonStyle.green
                else:
                    button_style = ButtonStyle.gray

                self.add_item(discord.ui.Button(
                    label=reason.value,
                    style=button_style,
                    disabled=response.banned is False,
                    custom_id=f"prr:reject:{response.community.id}:{response.player_report.id}:{reason.name}",
                    row=row
                ))

    @staticmethod
    async def get_embed(
        report: schemas.ReportWithToken,
        responses: list[schemas.PendingResponse],
        stats: dict[int, schemas.ResponseStats] = None
    ):
        embed = await get_report_embed(report, stats)
        if len(responses) == len(report.players):
            if any(response.banned for response in responses):
                embed.color = discord.Colour.brand_red()
            else:
                embed.color = discord.Colour.brand_green()
        return embed
