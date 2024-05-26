from abc import ABC, abstractmethod

import discord
from discord import ButtonStyle, Interaction
from discord.utils import escape_markdown as esc_md

from bunker import schemas
from bunker.crud.communities import admin_join_community, admin_leave_community, get_community_by_id, transfer_ownership, get_admin_by_id, create_new_admin
from bunker.db import models, session_factory
from bunker.constants import MAX_ADMIN_LIMIT
from bunker.discord.utils import View, CallableButton, CustomException, get_question_embed, get_danger_embed, get_success_embed

__all__ = (
    "AdminAddConfirmationView",
    "AdminRemoveConfirmationView",
    "OwnershipTransferConfirmationView",
    "LeaveCommunityConfirmationView",
)

class BaseConfirmationView(View, ABC):
    def __init__(self, community: models.Community, member: discord.Member):
        super().__init__(timeout=60*30)

        self.community = community
        self.member = member
        self.member_name = self.member.nick or self.member.display_name
        
        self.confirm_button = CallableButton(self.confirm, style=self.get_button_style(), label="Confirm", single_use=True)
        self.add_item(self.confirm_button)
    
    @staticmethod
    @abstractmethod
    def get_button_style(self) -> ButtonStyle:
        pass

    @abstractmethod
    async def send(self, interaction: Interaction):
        pass

    @abstractmethod
    async def confirm(self, interaction: Interaction):
        pass


class AdminAddConfirmationView(BaseConfirmationView):

    def get_button_style(self):
        return ButtonStyle.green
    
    async def send(self, interaction: Interaction):
        await interaction.response.send_message(embed=get_question_embed(
            title=esc_md(f"Do you want to add {self.member_name} as admin for {self.community.name}?"),
            description=f"Each community is allowed up to {MAX_ADMIN_LIMIT} admins, excluding the owner."
        ), view=self, ephemeral=True)

    async def confirm(self, interaction: Interaction):
        async with session_factory.begin() as db:
            admin = await get_admin_by_id(db, self.member.id)
            if admin:
                await admin_join_community(db, admin, self.community, by=interaction.user)
            else:
                await create_new_admin(db, schemas.AdminCreateParams(
                    discord_id=self.member.id,
                    community_id=self.community.id,
                    name=self.member.nick or self.member.display_name
                ))

        await interaction.response.edit_message(embed=get_success_embed(
            title=esc_md(f"Added {self.member_name} as admin for {self.community.name}!")
        ), view=None)

class AdminRemoveConfirmationView(BaseConfirmationView):

    def get_button_style(self):
        return ButtonStyle.green
    
    async def send(self, interaction: Interaction):
        await interaction.response.send_message(embed=get_question_embed(
            title=esc_md(f"Do you want to remove {self.member_name} as admin for {self.community.name}?"),
        ), view=self, ephemeral=True)

    async def confirm(self, interaction: Interaction):
        async with session_factory.begin() as db:
            admin = await get_admin_by_id(db, self.member.id)
            if not admin:
                raise CustomException("Admin not found!")
            if admin.community_id != self.community.id:
                raise CustomException("Admin is not part of your community!")
            await admin_leave_community(db, admin, by=interaction.user)

        await interaction.response.edit_message(embed=get_success_embed(
            title=esc_md(f"Removed {self.member_name} as admin for {self.community.name}!")
        ), view=None)

class OwnershipTransferConfirmationView(BaseConfirmationView):

    def get_button_style(self):
        return ButtonStyle.red
    
    async def send(self, interaction: Interaction):
        await interaction.response.send_message(embed=get_danger_embed(
            title=esc_md(f"Are you sure you want to transfer ownership of {self.community.name} to {self.member_name}?"),
            description="You will still remain an admin for the community, but can no longer add or remove admins."
        ), view=self, ephemeral=True)

    async def confirm(self, interaction: Interaction):
        async with session_factory.begin() as db:
            admin = await get_admin_by_id(db, self.member.id)
            if not admin:
                raise CustomException("Admin not found!")
            community = await get_community_by_id(db, self.community.id)
            if not community:
                raise CustomException("Community not found!")
            await transfer_ownership(db, community, admin, by=interaction.user)
            self.community = community

        await interaction.response.edit_message(embed=get_success_embed(
            title=esc_md(f"Transfered ownership of {self.community.name} to {self.member_name}!")
        ), view=None)

class LeaveCommunityConfirmationView(BaseConfirmationView):

    def get_button_style(self):
        return ButtonStyle.red
    
    async def send(self, interaction: Interaction):
        await interaction.response.send_message(embed=get_danger_embed(
            title=esc_md(f"Are you sure you want to unassociate yourself with {self.community.name}?"),
            description="You will lose your server admin role and access to private server admin channels."
        ), view=self, ephemeral=True)

    async def confirm(self, interaction: Interaction):
        async with session_factory.begin() as db:
            admin = await get_admin_by_id(db, self.member.id)
            if not admin:
                raise CustomException("Admin not found!")
            await admin_leave_community(db, admin, by=interaction.user)

        await interaction.response.edit_message(embed=get_success_embed(
            title=esc_md(f"You left {self.community.name}!")
        ), view=None)

