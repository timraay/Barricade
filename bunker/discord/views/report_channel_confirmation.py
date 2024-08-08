import discord
from discord import ButtonStyle, Interaction, TextChannel

from bunker.crud.communities import get_admin_by_id
from bunker.db import session_factory
from bunker.discord.utils import View, CallableButton, CustomException, get_question_embed, get_success_embed

class ReportChannelConfirmationView(View):
    def __init__(self, channel: TextChannel):
        super().__init__()
        self.channel = channel

        self.confirm_button = CallableButton(self.confirm, style=ButtonStyle.green, label="Confirm", single_use=True)
        self.add_item(self.confirm_button)

    async def send(self, interaction: Interaction):
        await interaction.response.send_message(embed=get_question_embed(
            title=f"Do you want to set `#{self.channel.name}` as your new report feed?",
            description="This channel should only be visible to your admins.",
        ), view=self, ephemeral=True)

    async def confirm(self, interaction: Interaction):
        async with session_factory.begin() as db:
            owner = await get_admin_by_id(db, interaction.user.id)
            if not owner or not owner.owned_community:
                raise CustomException(
                    "You need to be a community owner to do this!"
                )
            owner.community.forward_guild_id = self.channel.guild.id
            owner.community.forward_channel_id = self.channel.id

            if (
                owner.community.admin_role_id
                and not discord.utils.get(
                    self.channel.guild.roles,
                    id=owner.community.admin_role_id
                )
            ):
                # If the admin role is no longer part of the updated guild, remove it
                owner.community.admin_role_id = None

            await interaction.response.edit_message(embed=get_success_embed(
                title=f"Set `#{self.channel.name}` as the new report feed for {owner.community.name}!"
            ), view=None)