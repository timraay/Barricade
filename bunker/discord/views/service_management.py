from functools import partial

import discord
from discord import ButtonStyle, Interaction

from bunker import schemas
from bunker.db import models, session_factory
from bunker.communities import get_admin_by_id
from bunker.discord.utils import View, CallableButton, CustomException

SERVICE_TYPES: tuple[type[schemas.ServiceConfig]] = (
    schemas.BattlemetricsServiceConfig,
    schemas.CRCONServiceConfig,
)

class ServiceManagementView(View):
    def __init__(self, community: models.Community):
        super().__init__(timeout=60*30)
        self.community = community

    def get_embed_update_self(self):
        embed = discord.Embed()
        self.clear_items()

        enabled = 0
        for row, service_type in enumerate(SERVICE_TYPES):
            config = service_type.create(self.community)

            if config is None:
                value = "`Unconfigured` \⚫"

                self.add_item(discord.ui.Button(
                    style=ButtonStyle.gray,
                    emoji=service_type.emoji,
                    disabled=True,
                    row=row
                ))
                self.add_item(CallableButton(
                    partial(self.configure_service, service_type),
                    style=ButtonStyle.gray,
                    label="Configure...",
                    row=row
                ))

            elif not config.enabled:
                value = "`Disabled` \🔴"

                self.add_item(discord.ui.Button(
                    style=ButtonStyle.red,
                    emoji=service_type.emoji,
                    url=config.get_url(),
                    row=row
                ))
                self.add_item(CallableButton(
                    partial(self.enable_service, service_type),
                    style=ButtonStyle.gray,
                    label="Enable",
                    row=row
                ))
                self.add_item(CallableButton(
                    partial(self.configure_service, service_type),
                    style=ButtonStyle.gray,
                    label="Reconfigure...",
                    row=row
                ))

            else:
                value = "**`Enabled`** \🟢"

                self.add_item(discord.ui.Button(
                    style=ButtonStyle.green,
                    emoji=service_type.emoji,
                    url=config.get_url(),
                    row=row
                ))
                self.add_item(CallableButton(
                    partial(self.disable_service, service_type),
                    style=ButtonStyle.gray,
                    label="Enable",
                    row=row
                ))
                self.add_item(CallableButton(
                    partial(self.configure_service, service_type),
                    style=ButtonStyle.gray,
                    label="Reconfigure...",
                    row=row
                ))

                enabled += 1
            
            embed.add_field(name=service_type.name, value=value, inline=True)
        
        embed.title = f"Connected services ({enabled})"
        return embed

    async def send(self, interaction: discord.Interaction):
        embed = self.get_embed_update_self()
        await interaction.response.send_message(embed=embed, view=self, ephemeral=True)
    
    async def edit(self, interaction: discord.Interaction):
        embed = self.get_embed_update_self()
        await interaction.response.edit_message(embed=embed, view=self)

    async def configure_service(self, service_type: type[schemas.ServiceConfig], interaction: Interaction):
        pass

    async def enable_service(self, service_type: type[schemas.ServiceConfig], interaction: Interaction):
        pass

    async def disable_service(self, service_type: type[schemas.ServiceConfig], interaction: Interaction):
        pass
