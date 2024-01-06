from functools import partial
import re
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from uuid import UUID

import discord
from discord import ButtonStyle, Interaction

from bunker import schemas
from bunker.db import models, session_factory
from bunker.communities import get_admin_by_id
from bunker.discord.utils import View, Modal, CallableButton, CustomException, get_success_embed
from bunker.services import Service, BattlemetricsService, CRCONService

RE_BATTLEMETRICS_ORG_URL = re.compile(r"https://www.battlemetrics.com/rcon/orgs/edit/(\d+)")

SERVICE_TYPES: tuple[type[schemas.ServiceConfig]] = (
    schemas.BattlemetricsServiceConfig,
    schemas.CRCONServiceConfig,
)

async def get_owned_community(db: AsyncSession, user_id: int):
    owner = await get_admin_by_id(db, user_id)
    if not owner or not owner.owned_community:
        raise CustomException(
            "You need to be a community owner to do this!",
        )
    return owner.community

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
                    label="Disable",
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

    async def send(self, interaction: Interaction):
        embed = self.get_embed_update_self()
        await interaction.response.send_message(embed=embed, view=self, ephemeral=True)
        self.message = await interaction.original_response()
    
    async def edit(self, interaction: Optional[Interaction] = None):
        embed = self.get_embed_update_self()
        if interaction:
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await self.message.edit(embed=embed, view=self)

    async def configure_service(self, service_type: type[schemas.ServiceConfig], interaction: Interaction):
        async with session_factory() as db:
            community = await get_owned_community(db, interaction.user.id)

        if service_type is schemas.BattlemetricsServiceConfig:
            modal = ConfigureBattlemetricsServiceModal(
                view=self,
                default_values=community.battlemetrics_service
            )
        elif service_type is schemas.CRCONServiceConfig:
            modal = ConfigureCRCONServiceModal(
                view=self,
                default_values=community.crcon_service
            )
        else:
            raise TypeError("Unexpected type %s" % service_type.__name__)
        
        await interaction.response.send_modal(modal)

    async def enable_service(self, service_type: type[schemas.ServiceConfig], interaction: Interaction):
        async with session_factory() as db:
            community = await get_owned_community(db, interaction.user.id)
        
            # Get service
            if service_type is schemas.BattlemetricsServiceConfig:
                config = schemas.BattlemetricsServiceConfig.model_validate(community.battlemetrics_service)
                service = BattlemetricsService(config)
            elif service_type is schemas.CRCONServiceConfig:
                config = schemas.CRCONServiceConfig.model_validate(community.crcon_service)
                service = CRCONService(config)
            else:
                raise TypeError("Unexpected type %s" % service_type.__name__)

            # Validate config
            try:
                await service.validate(community)
            except Exception as e:
                raise CustomException("Outdated service configuration!", str(e), log_traceback=True)
            
            self.community = await service.enable(db, community)

        await interaction.response.send_message(embed=get_success_embed(
            f"Enabled {service.config.name} service!"
        ), ephemeral=True)
        await self.edit()

    async def disable_service(self, service_type: type[schemas.ServiceConfig], interaction: Interaction):
        async with session_factory() as db:
            community = await get_owned_community(db, interaction.user.id)
        
            # Get service
            if service_type is schemas.BattlemetricsServiceConfig:
                config = schemas.BattlemetricsServiceConfig.model_validate(community.battlemetrics_service)
                service = BattlemetricsService(config)
            elif service_type is schemas.CRCONServiceConfig:
                config = schemas.CRCONServiceConfig.model_validate(community.crcon_service)
                service = CRCONService(config)
            else:
                raise TypeError("Unexpected type %s" % service_type.__name__)

            self.community = await service.disable(db, community)

        await interaction.response.send_message(embed=get_success_embed(
            f"Disabled {service.config.name} service!"
        ), ephemeral=True)
        await self.edit()


async def submit_service_config(interaction: Interaction, service: Service):
    # Defer the interaction in case below steps take longer than 3 seconds
    await interaction.response.defer(ephemeral=True, thinking=True)

    async with session_factory() as db:
        # Make sure user is owner
        owner = await get_admin_by_id(db, interaction.user.id)
        if not owner or not owner.owned_community:
            raise CustomException(
                "You need to be a community owner to do this!",
            )
        community = owner.community

        # Validate config
        service.config.community_id = community.id
        try:
            await service.validate(community)
        except Exception as e:
            raise CustomException("Failed to configure service!", str(e))
        
        # Update config in DB
        community = await service.save_config(db, community)

    await interaction.followup.send(embed=get_success_embed(
        f"Configured {service.config.name} service!"
    ))

    return community

class ConfigureBattlemetricsServiceModal(Modal):
    # Define input fields
    api_key = discord.ui.TextInput(
        label="API key",
        style=discord.TextStyle.short,
    )

    org_url = discord.ui.TextInput(
        label="Organization URL",
        style=discord.TextStyle.short,
        placeholder="https://www.battlemetrics.com/rcon/orgs/edit/...",
    )

    banlist_id = discord.ui.TextInput(
        label="Banlist ID (Leave empty to create new)",
        style=discord.TextStyle.short,
        required=False
    )

    def __init__(self, view: ServiceManagementView, default_values: Optional[schemas.BattlemetricsServiceConfig] = None):
        super().__init__(
            title="Configure Battlemetrics Service",
            timeout=None
        )
        self.view = view

        # Load default values
        if default_values:
            self.api_key.default = default_values.api_key
            self.org_url.default = "https://www.battlemetrics.com/rcon/orgs/edit/" + str(default_values.organization_id)
            self.banlist_id.default = str(default_values.banlist_id)
        else:
            self.api_key.default = None
            self.org_url.default = None
            self.banlist_id.default = None

    async def on_submit(self, interaction: Interaction):
        # Extract organization ID
        match = RE_BATTLEMETRICS_ORG_URL.match(self.org_url.value)
        if not match:
            raise CustomException("Invalid organization URL!")
        organization_id = match.group(1)

        # Cast banlist_id to UUID
        if self.banlist_id.value:
            try:
                banlist_id = UUID(self.banlist_id.value)
            except ValueError:
                raise CustomException("Invalid banlist ID!")
        else:
            banlist_id = None

        config = schemas.BattlemetricsServiceConfig(
            community_id=0, # Update this later
            api_key=self.api_key.value,
            organization_id=organization_id,
            banlist_id=banlist_id
        )
        service = BattlemetricsService(config)
        
        community = await submit_service_config(interaction, service)
        self.view.community = community

class ConfigureCRCONServiceModal(Modal):
    # Define input fields
    api_url = discord.ui.TextInput(
        label="API URL",
        style=discord.TextStyle.short,
        default="http://........../api"
    )
    
    api_key = discord.ui.TextInput(
        label="API key",
        style=discord.TextStyle.short,
    )

    def __init__(self, view: ServiceManagementView, default_values: Optional[schemas.CRCONServiceConfig] = None):
        super().__init__(
            title="Configure Community RCON Service",
            timeout=None
        )
        self.view = view

        # Load default values
        if default_values:
            self.api_key.default = default_values.api_url
            self.api_key.default = default_values.api_key
        else:
            self.api_url.default = None
            self.api_key.default = None

    async def on_submit(self, interaction: Interaction):
        config = schemas.CRCONServiceConfig(
            community_id=0, # Update this later
            api_url=self.api_url.value,
            api_key=self.api_key.value,
        )
        service = CRCONService(config)

        community = await submit_service_config(interaction, service)
        self.view.community = community
