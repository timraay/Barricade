import discord
from discord import ButtonStyle, Interaction

from bunker import schemas
from bunker.crud.communities import get_admin_by_id
from bunker.crud.reports import create_token, get_form_url
from bunker.db import session_factory
from bunker.discord.utils import View, CallableButton, CustomException

class GetSubmissionURLView(View):
    def __init__(self):
        super().__init__(timeout=None)

        self.add_item(CallableButton(
            self.start_submission,
            style=ButtonStyle.blurple,
            label="Get submission URL",
            custom_id="get_submission_url"
        ))

    async def start_submission(self, interaction: Interaction):
        async with session_factory() as db:
            admin = await get_admin_by_id(db, interaction.user.id)
            if not admin or not admin.community_id:
                raise CustomException("Only verified server admins can create reports!")
            
            # Update stored name
            name = interaction.user.nick or interaction.user.display_name
            if admin.name != name:
                admin.name = name

            token = schemas.ReportTokenCreateParams(
                admin_id=admin.discord_id,
                community_id=admin.community_id
            )
            db_token = await create_token(db, token)
        
        url = get_form_url(db_token.value)
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
