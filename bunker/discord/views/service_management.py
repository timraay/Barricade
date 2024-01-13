from functools import partial
from pydantic import BaseModel
import re
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, Callable, Coroutine, Any
from uuid import UUID

import discord
from discord import ButtonStyle, Interaction
from discord.utils import escape_markdown as esc_md

from bunker import schemas
from bunker.db import models, session_factory
from bunker.communities import get_admin_by_id, update_service_config, create_service_config
from bunker.discord.utils import View, Modal, CallableButton, CustomException, get_success_embed
from bunker.services import Service, BattlemetricsService, CRCONService

RE_BATTLEMETRICS_ORG_URL = re.compile(r"https://www.battlemetrics.com/rcon/orgs/edit/(\d+)")

SERVICE_TYPES: tuple[type[schemas.ServiceConfig]] = (
    schemas.BattlemetricsServiceConfig,
    schemas.CRCONServiceConfig,
)

class ServiceProperties(BaseModel):
    config_cls: type[schemas.ServiceConfig]
    service_cls: type[Service]
    configure_func: Callable[[schemas.ServiceConfig | None], Coroutine[Any, Any, None]]
    name: str
    emoji: str
    url_func: Callable[[schemas.ServiceConfig], str]


async def configure_battlemetrics_service(interaction: Interaction, view: 'ServiceManagementView', config: schemas.BattlemetricsServiceConfig | None):
    modal = ConfigureBattlemetricsServiceModal(view, default_values=config)
    await interaction.response.send_modal(modal)

async def configure_crcon_service(interaction: Interaction, view: 'ServiceManagementView', config: schemas.CRCONServiceConfig | None):
    modal = ConfigureCRCONServiceModal(view, default_values=config)
    await interaction.response.send_modal(modal)


SERVICE_PROPERTIES = {
    schemas.BattlemetricsServiceConfig.service_type: ServiceProperties(
        config_cls=schemas.BattlemetricsServiceConfig,
        service_cls=BattlemetricsService,
        configure_func=configure_battlemetrics_service,
        name="Battlemetrics",
        emoji="🤕",
        url_func=lambda config: f"https://battlemetrics.com/rcon/orgs/{config.organization_id}/edit",
    ),
    schemas.CRCONServiceConfig.service_type: ServiceProperties(
        config_cls=schemas.CRCONServiceConfig,
        service_cls=CRCONService,
        configure_func=configure_crcon_service,
        name="Community RCON",
        emoji="🤩",
        url_func=lambda config: config.api_url.removesuffix("/api"),
    ),
}

async def get_owned_community(db: AsyncSession, user_id: int):
    owner = await get_admin_by_id(db, user_id)
    if not owner or not owner.owned_community:
        raise CustomException(
            "You need to be a community owner to do this!",
        )
    return owner.community

async def get_config_from_community(community: models.Community, service_id: int):
    for service in community.services:
        if service.id == service_id:
            return service
    raise CustomException("This service no longer exists")


async def get_name_hyperlink(service: Service):
    properties = SERVICE_PROPERTIES[service.config.service_type]
    try:
        name = esc_md(await service.get_instance_name())
    except:
        name = "*Name unknown*"

    return f"[**{name}** 🡥]({properties.url_func(service.config)})"


class ServiceManagementView(View):
    def __init__(self, community: models.Community):
        super().__init__(timeout=60*30)
        self.community = community

    def get_services(self) -> list[Service]:
        services = []
        for db_service in self.community.services:
            properties = SERVICE_PROPERTIES[db_service.service_type]
            config = properties.config_cls.model_validate(db_service)
            service = properties.service_cls(config)
            services.append(service)
        return services

    async def get_embed_update_self(self):
        embed = discord.Embed()
        self.clear_items()

        row = 0
        enabled = 0
        for row, service in enumerate(self.get_services()):
            properties = SERVICE_PROPERTIES[service.config.service_type]
            enabled = service.config.enabled
            name = await get_name_hyperlink(service)

            self.add_item(discord.ui.Button(
                style=ButtonStyle.green if enabled else ButtonStyle.gray,
                emoji=properties.emoji,
                label=f"# {row+1}.",
                row=row,
                disabled=True
            ))

            if service.config.enabled:
                embed.add_field(
                    name=f"{row+1}. {properties.name}",
                    value=f"{name}\n**`Enabled`** \🟢",
                    inline=True
                )
            
                self.add_item(CallableButton(
                    partial(self.disable_service, service.config.id),
                    style=ButtonStyle.blurple,
                    label="Disable",
                    row=row
                ))

                enabled += 1

            else:
                embed.add_field(
                    name=f"{row+1}. {properties.name}",
                    value=f"{name}\n`Disabled` \🔴",
                    inline=True
                )

                self.add_item(CallableButton(
                    partial(self.enable_service, service.config.id),
                    style=ButtonStyle.green,
                    label="Enable",
                    row=row
                ))
            
            self.add_item(CallableButton(
                partial(self.configure_service, properties, service.config.id),
                style=ButtonStyle.gray if enabled else ButtonStyle.blurple,
                label="Reconfigure",
                row=row
            ))

        self.add_item(CallableButton(
            self.add_service,
            style=ButtonStyle.gray,
            label="Add service...",
            row=row + 1
        ))

        embed.title = f"Connected services ({enabled})"
        return embed

    async def send(self, interaction: Interaction):
        to_validate = [
            service for service in self.get_services()
            if service.config.enabled
        ]

        if to_validate:
            embed = discord.Embed(
                description=f"Validating {len(to_validate)} service(s)...",
                colour=discord.Colour(0x2b2d31)
            )
            await interaction.response.send_message(embed=embed, view=self, ephemeral=True)
            self.message = await interaction.original_response()
        
            for service in to_validate:
                try:
                    await service.validate(self.community)
                except:
                    async with session_factory() as db:
                        await service.disable(db)
            
            await self.edit()
        
        else:
            embed = await self.get_embed_update_self()
            await interaction.response.send_message(embed=embed, view=self, ephemeral=True)
            self.message = await interaction.original_response()
    
    async def edit(self, interaction: Optional[Interaction] = None):
        embed = await self.get_embed_update_self()
        if interaction:
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await self.message.edit(embed=embed, view=self)

    async def configure_service(self, properties: ServiceProperties, service_id: int | None, interaction: Interaction):
        async with session_factory() as db:
            community = await get_owned_community(db, interaction.user.id)

        if service_id:
            config = await get_config_from_community(community, service_id)
        else:
            config = None

        await properties.configure_func(interaction, self, config)

    async def enable_service(self, service_id: int, interaction: Interaction):
        async with session_factory() as db:
            community = await get_owned_community(db, interaction.user.id)
            db_config = await get_config_from_community(community, service_id)

            properties = SERVICE_PROPERTIES[db_config.service_type]
            config = properties.config_cls.model_validate(db_config)
            service = properties.service_cls(config)

            # Validate config
            try:
                await service.validate(community)
            except Exception as e:
                raise CustomException("Outdated service configuration!", str(e), log_traceback=True)
            
            self.community = await service.enable(db, community)

        await interaction.response.send_message(embed=get_success_embed(
            f"Enabled {properties.name} service!",
            await get_name_hyperlink(service)
        ), ephemeral=True)
        await self.edit()

    async def disable_service(self, service_id: int, interaction: Interaction):
        async with session_factory() as db:
            community = await get_owned_community(db, interaction.user.id)
            db_config = await get_config_from_community(community, service_id)

            properties = SERVICE_PROPERTIES[db_config.service_type]
            config = properties.config_cls.model_validate(db_config)
            service = properties.service_cls(config)

            self.community = await service.disable(db, community)

        await interaction.response.send_message(embed=get_success_embed(
            f"Disabled {properties.name} service!",
            await get_name_hyperlink(service)
        ), ephemeral=True)
        await self.edit()

    async def add_service(self, interaction: Interaction):
        self.clear_items()
        for properties in SERVICE_PROPERTIES.values():
            self.add_item(CallableButton(
                partial(self.configure_service, properties, None),
                style=ButtonStyle.blurple,
                label=properties.name,
                emoji=properties.emoji,
                row=0
            ))
        self.add_item(CallableButton(
            self.edit,
            style=ButtonStyle.gray,
            label="Back...",
            row=1
        ))
        await interaction.response.edit_message(view=self)

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
        await service.save_config(db)
        await db.refresh(community)

    await interaction.followup.send(embed=get_success_embed(
        f"Configured {SERVICE_PROPERTIES[service.config.service_type].name} service!"
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
            self.service_id = default_values.id
            self.api_key.default = default_values.api_key
            self.org_url.default = "https://www.battlemetrics.com/rcon/orgs/edit/" + str(default_values.organization_id)
            self.banlist_id.default = str(default_values.banlist_id)
        else:
            self.service_id = None
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

        config = schemas.BattlemetricsServiceConfigParams(
            id=self.service_id,
            community_id=0, # Update this later
            api_key=self.api_key.value,
            organization_id=organization_id,
            banlist_id=banlist_id
        )
        service = BattlemetricsService(config)
        
        community = await submit_service_config(interaction, service)
        self.view.community = community
        await self.view.edit()

class ConfigureCRCONServiceModal(Modal):
    # Define input fields
    api_url = discord.ui.TextInput(
        label="API URL",
        style=discord.TextStyle.short,
        default="https://........../api"
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
            self.service_id = default_values.id
            self.api_key.default = default_values.api_url
            self.api_key.default = default_values.api_key
        else:
            self.service_id = None
            self.api_url.default = None
            self.api_key.default = None

    async def on_submit(self, interaction: Interaction):
        config = schemas.CRCONServiceConfig(
            id=self.service_id,
            community_id=0, # Update this later
            api_url=self.api_url.value,
            api_key=self.api_key.value,
        )
        service = CRCONService(config)

        community = await submit_service_config(interaction, service)
        self.view.community = community
        await self.view.edit()

