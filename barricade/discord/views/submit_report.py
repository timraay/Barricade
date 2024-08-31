import discord
from discord import ButtonStyle, Interaction

from barricade import schemas
from barricade.crud.communities import get_admin_by_id
from barricade.crud.reports import create_token
from barricade.db import session_factory
from barricade.discord.utils import View, CallableButton, CustomException
from barricade.enums import Platform
from barricade.urls import get_report_create_url

class GetSubmissionURLView(View):
    def __init__(self, platform: Platform):
        super().__init__(timeout=None)
        self.platform = platform

        if platform == Platform.PC:
            custom_id = "get_submission_url_pc"
        elif platform == Platform.CONSOLE:
            custom_id = "get_submission_url_console"
        else:
            raise TypeError("Unknown platform %r" % platform)

        self.add_item(CallableButton(
            self.start_submission,
            style=ButtonStyle.blurple,
            label="Get submission URL",
            custom_id=custom_id
        ))

    async def start_submission(self, interaction: Interaction):
        async with session_factory.begin() as db:
            admin = await get_admin_by_id(db, interaction.user.id)
            if not admin or not admin.community_id:
                raise CustomException("Only verified server admins can create reports!")
            
            # Update stored name
            user: discord.Member = interaction.user # type: ignore
            name = user.nick or user.display_name
            if admin.name != name:
                admin.name = name

            token = schemas.ReportTokenCreateParams(
                admin_id=admin.discord_id,
                community_id=admin.community_id,
                platform=self.platform,
            )
            db_token = await create_token(db, token)
        
        url = get_report_create_url(db_token.value)
        view = OpenFormView(url)
        await view.send(interaction)


class OpenFormView(View):
    def __init__(self, url: str):
        super().__init__()
        self.add_item(discord.ui.Button(
            style=ButtonStyle.blurple,
            label="Open Form",
            url=url
        ))

    async def send(self, interaction: Interaction):
        await interaction.response.send_message(view=self, ephemeral=True)
