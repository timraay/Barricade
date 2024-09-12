from discord import Interaction, SelectOption, ButtonStyle

from barricade import schemas
from barricade.crud.communities import get_admin_by_id
from barricade.discord.utils import CallableButton, CallableSelect, CustomException, View
from barricade.db import session_factory
from barricade.enums import ReportReasonDetails, ReportReasonFlag


class ReasonsFilterView(View):
    def __init__(self, community: schemas.CommunityRef):
        super().__init__(timeout=300)
        self.community = community
        self.is_custom = bool(community.reasons_filter)

        self.select_all_button = CallableButton(self.select_all, label="All")
        self.select_none_button = CallableButton(self.select_none, label="None")
        self.select_custom_button = CallableButton(self.select_custom, label="Custom...")

        self.select_reasons_select = CallableSelect(
            self.select_reasons,
            options=[
                SelectOption(
                    label=reason.value.pretty_name, # type: ignore
                    emoji=reason.value.emoji, # type: ignore
                    value=str(ReportReasonFlag[reason.name].value),
                )
                for reason in ReportReasonDetails
            ] + [
                SelectOption(
                    label="Custom",
                    emoji="🎲",
                    value=str(ReportReasonFlag.CUSTOM.value),
                )
            ],
            min_values=1,
            max_values=len(ReportReasonFlag),
        )

        self.add_item(self.select_all_button)
        self.add_item(self.select_none_button)
        self.add_item(self.select_custom_button)

        self.update_item_state()

    def update_item_state(self):
        self.select_all_button.disabled = False
        self.select_none_button.disabled = False
        self.select_custom_button.disabled = False
        self.select_all_button.style = ButtonStyle.gray
        self.select_none_button.style = ButtonStyle.gray
        self.select_custom_button.style = ButtonStyle.gray
        self.remove_item(self.select_reasons_select)

        if self.community.reasons_filter is None:
            self.select_all_button.disabled = True
            self.select_all_button.style = ButtonStyle.green
        elif self.is_custom:
            self.select_custom_button.disabled = True
            self.select_custom_button.style = ButtonStyle.blurple
            self.add_item(self.select_reasons_select)
            for option in self.select_reasons_select.options:
                option.default = (int(option.value) & self.community.reasons_filter) != 0
        elif self.community.reasons_filter == 0:
            self.select_none_button.disabled = True
            self.select_none_button.style = ButtonStyle.green
        
    async def send(self, interaction: Interaction):
        await interaction.response.send_message(
            content="Select which categories of reports you want your community to receive.",
            view=self,
            ephemeral=True,
        )

    async def edit(self, interaction: Interaction):
        await interaction.response.edit_message(view=self)

    async def persist_filter(self, interaction: Interaction, filter: ReportReasonFlag | None):
        async with session_factory.begin() as db:
            db_admin = await get_admin_by_id(db, interaction.user.id)
            if not db_admin or not db_admin.community or db_admin.community_id != self.community.id:
                raise CustomException(
                    "You need to be a community admin to do this!"
                )
            db_admin.community.reasons_filter = filter
        self.community.reasons_filter = filter

    async def select_all(self, interaction: Interaction):
        await self.persist_filter(interaction, None)
        self.is_custom = False
        self.update_item_state()
        await self.edit(interaction)

    async def select_none(self, interaction: Interaction):
        await self.persist_filter(interaction, ReportReasonFlag(0))
        self.is_custom = False
        self.update_item_state()
        await self.edit(interaction)

    async def select_custom(self, interaction: Interaction):
        if self.community.reasons_filter is None:
            await self.persist_filter(interaction, ReportReasonFlag.all())
        else:
            await self.persist_filter(interaction, ReportReasonFlag(0))

        self.is_custom = True
        self.update_item_state()

        await self.edit(interaction)

    async def select_reasons(self, interaction: Interaction, values: list[str]):
        filter = ReportReasonFlag(0)
        for value in values:
            filter |= int(value)

        await self.persist_filter(interaction, filter)
        await interaction.response.defer()

    

