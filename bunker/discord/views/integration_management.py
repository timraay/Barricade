import asyncio
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
from bunker.communities import get_admin_by_id
from bunker.discord.utils import View, Modal, CallableButton, CustomException, get_success_embed, get_question_embed, only_once
from bunker.integrations import Integration, BattlemetricsIntegration, CRCONIntegration

RE_BATTLEMETRICS_ORG_URL = re.compile(r"https://www.battlemetrics.com/rcon/orgs/edit/(\d+)")

INTEGRATION_TYPES: tuple[type[schemas.IntegrationConfig]] = (
    schemas.BattlemetricsIntegrationConfig,
    schemas.CRCONIntegrationConfig,
)

class IntegrationProperties(BaseModel):
    config_cls: type[schemas.IntegrationConfig]
    integration_cls: type[Integration]
    configure_func: Callable[[schemas.IntegrationConfig | None], Coroutine[Any, Any, None]]
    ask_remove_bans: bool
    name: str
    emoji: str
    url_func: Callable[[schemas.IntegrationConfig], str]


async def configure_battlemetrics_integration(interaction: Interaction, view: 'IntegrationManagementView', config: schemas.BattlemetricsIntegrationConfig | None):
    modal = ConfigureBattlemetricsIntegrationModal(view, default_values=config)
    await interaction.response.send_modal(modal)

async def configure_crcon_integration(interaction: Interaction, view: 'IntegrationManagementView', config: schemas.CRCONIntegrationConfig | None):
    modal = ConfigureCRCONIntegrationModal(view, default_values=config)
    await interaction.response.send_modal(modal)


INTEGRATION_PROPERTIES = {
    schemas.BattlemetricsIntegrationConfig.integration_type: IntegrationProperties(
        config_cls=schemas.BattlemetricsIntegrationConfig,
        integration_cls=BattlemetricsIntegration,
        configure_func=configure_battlemetrics_integration,
        ask_remove_bans=False,
        name="Battlemetrics",
        emoji="🤕",
        url_func=lambda config: f"https://battlemetrics.com/rcon/orgs/{config.organization_id}/edit",
    ),
    schemas.CRCONIntegrationConfig.integration_type: IntegrationProperties(
        config_cls=schemas.CRCONIntegrationConfig,
        integration_cls=CRCONIntegration,
        configure_func=configure_crcon_integration,
        ask_remove_bans=True,
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

async def get_config_from_community(community: models.Community, integration_id: int):
    for integration in community.integrations:
        if integration.id == integration_id:
            return integration
    raise CustomException("This integration no longer exists")


async def get_name_hyperlink(integration: Integration):
    properties = INTEGRATION_PROPERTIES[integration.config.integration_type]
    try:
        name = esc_md(await integration.get_instance_name())
    except:
        name = "*Name unknown*"

    return f"[**{name}** 🡥]({properties.url_func(integration.config)})"


class IntegrationManagementView(View):
    def __init__(self, community: models.Community):
        super().__init__(timeout=60*30)
        self.community = community

    def get_integrations(self) -> list[Integration]:
        integrations = []
        for db_integration in self.community.integrations:
            properties = INTEGRATION_PROPERTIES[db_integration.integration_type]
            config = properties.config_cls.model_validate(db_integration)
            integration = properties.integration_cls(config)
            integrations.append(integration)
        return integrations

    async def get_embed_update_self(self):
        embed = discord.Embed()
        self.clear_items()

        row = 0
        enabled = 0
        for row, integration in enumerate(self.get_integrations()):
            properties = INTEGRATION_PROPERTIES[integration.config.integration_type]
            enabled = integration.config.enabled
            name = await get_name_hyperlink(integration)

            self.add_item(discord.ui.Button(
                style=ButtonStyle.green if enabled else ButtonStyle.gray,
                emoji=properties.emoji,
                label=f"# {row+1}.",
                row=row,
                disabled=True
            ))

            if integration.config.enabled:
                embed.add_field(
                    name=f"{row+1}. {properties.name}",
                    value=f"{name}\n**`Enabled`** \🟢",
                    inline=True
                )
            
                self.add_item(CallableButton(
                    partial(self.disable_integration, integration.config.id),
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
                    partial(self.enable_integration, integration.config.id),
                    style=ButtonStyle.green,
                    label="Enable",
                    row=row
                ))
            
            self.add_item(CallableButton(
                partial(self.configure_integration, properties, integration.config.id),
                style=ButtonStyle.gray if enabled else ButtonStyle.blurple,
                label="Reconfigure",
                row=row
            ))

        self.add_item(CallableButton(
            self.add_integration,
            style=ButtonStyle.gray,
            label="Add integration...",
            row=row + 1
        ))

        embed.title = f"Connected integrations ({enabled})"
        return embed

    async def send(self, interaction: Interaction):
        to_validate = [
            integration for integration in self.get_integrations()
            if integration.config.enabled
        ]

        if to_validate:
            embed = discord.Embed(
                description=f"Validating {len(to_validate)} integration(s)...",
                colour=discord.Colour(0x2b2d31)
            )
            await interaction.response.send_message(embed=embed, view=self, ephemeral=True)
            self.message = await interaction.original_response()
        
            for integration in to_validate:
                try:
                    await integration.validate(self.community)
                except:
                    async with session_factory() as db:
                        await integration.disable(db)
            
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

    async def configure_integration(self, properties: IntegrationProperties, integration_id: int | None, interaction: Interaction):
        async with session_factory() as db:
            community = await get_owned_community(db, interaction.user.id)

        if integration_id:
            config = await get_config_from_community(community, integration_id)
        else:
            config = None

        await properties.configure_func(interaction, self, config)

    async def enable_integration(self, integration_id: int, interaction: Interaction):
        async with session_factory() as db:
            community = await get_owned_community(db, interaction.user.id)
            db_config = await get_config_from_community(community, integration_id)

            properties = INTEGRATION_PROPERTIES[db_config.integration_type]
            config = properties.config_cls.model_validate(db_config)
            integration = properties.integration_cls(config)

            # Validate config
            try:
                await integration.validate(community)
            except Exception as e:
                raise CustomException("Outdated integration configuration!", str(e), log_traceback=True)
            
            self.community = await integration.enable(db, community)

        await interaction.response.send_message(embed=get_success_embed(
            f"Enabled {properties.name} integration!",
            await get_name_hyperlink(integration)
        ), ephemeral=True)
        await self.edit()

    async def disable_integration(self, integration_id: int, interaction: Interaction):
        async with session_factory() as db:
            community = await get_owned_community(db, interaction.user.id)
            db_config = await get_config_from_community(community, integration_id)

            properties = INTEGRATION_PROPERTIES[db_config.integration_type]
            config = properties.config_cls.model_validate(db_config)
            integration = properties.integration_cls(config)

            if properties.ask_remove_bans:
                try:
                    remove_bans = await ask_remove_bans(interaction)
                except asyncio.TimeoutError:
                    return

            self.community = await integration.disable(db, community, remove_bans=remove_bans)
        
        embed = get_success_embed(
            f"Disabled {properties.name} integration!",
            await get_name_hyperlink(integration)
        )
        if interaction.response.is_done():
            await interaction.response.edit_message(embed=embed, view=None)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
        await self.edit()

    async def add_integration(self, interaction: Interaction):
        self.clear_items()
        for properties in INTEGRATION_PROPERTIES.values():
            self.add_item(CallableButton(
                partial(self.configure_integration, properties, None),
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

class AskRemoveBansView(View):
    def __init__(self, fut: asyncio.Future):
        self.fut = fut
        self.add_item(CallableButton(partial(self.submit, True), label="Yes", style=ButtonStyle.blurple))
        self.add_item(CallableButton(partial(self.submit, False), label="No", style=ButtonStyle.blurple))

    @only_once
    async def submit(self, remove_bans: bool, interaction: Interaction):
        await interaction.response.defer()
        self.fut.set_result(remove_bans)
    
    async def on_timeout(self):
        self.fut.set_exception(asyncio.TimeoutError())

async def ask_remove_bans(interaction: Interaction):
    fut = asyncio.get_running_loop().create_future()
    view = AskRemoveBansView(fut)
    await interaction.response.send_message(embed=get_question_embed(
        "Do you want to unban all players?",
        (
            "You are about to disconnect this integration from Bunker, meaning it will no longer"
            "receive updates from Bunker on what players to ban or unban."
            "\n\n"
            "If you want this integration to remove all its Bunker bans, press \"Yes\". If you want"
            " them to remain in place, press \"No\"."
            "\n\n"
            "This decision does not affect any other integrations. Should you choose \"Yes\", you"
            " will always be able to import your bans again in the future."
        )
    ), view=view)
    return await fut

async def submit_integration_config(interaction: Interaction, integration: Integration):
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
        integration.config.community_id = community.id
        try:
            await integration.validate(community)
        except Exception as e:
            raise CustomException("Failed to configure integration!", str(e))
        
        # Update config in DB
        await integration.save_config(db)
        await db.refresh(community)

    await interaction.followup.send(embed=get_success_embed(
        f"Configured {INTEGRATION_PROPERTIES[integration.config.integration_type].name} integration!"
    ))

    return community

class ConfigureBattlemetricsIntegrationModal(Modal):
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

    def __init__(self, view: IntegrationManagementView, default_values: Optional[schemas.BattlemetricsIntegrationConfig] = None):
        super().__init__(
            title="Configure Battlemetrics Integration",
            timeout=None
        )
        self.view = view

        # Load default values
        if default_values:
            self.integration_id = default_values.id
            self.api_key.default = default_values.api_key
            self.org_url.default = "https://www.battlemetrics.com/rcon/orgs/edit/" + str(default_values.organization_id)
            self.banlist_id.default = str(default_values.banlist_id)
        else:
            self.integration_id = None
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

        config = schemas.BattlemetricsIntegrationConfigParams(
            id=self.integration_id,
            community_id=0, # Update this later
            api_key=self.api_key.value,
            organization_id=organization_id,
            banlist_id=banlist_id
        )
        integration = BattlemetricsIntegration(config)
        
        community = await submit_integration_config(interaction, integration)
        self.view.community = community
        await self.view.edit()

class ConfigureCRCONIntegrationModal(Modal):
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

    def __init__(self, view: IntegrationManagementView, default_values: Optional[schemas.CRCONIntegrationConfig] = None):
        super().__init__(
            title="Configure Community RCON Integration",
            timeout=None
        )
        self.view = view

        # Load default values
        if default_values:
            self.integration_id = default_values.id
            self.api_key.default = default_values.api_url
            self.api_key.default = default_values.api_key
        else:
            self.integration_id = None
            self.api_url.default = None
            self.api_key.default = None

    async def on_submit(self, interaction: Interaction):
        config = schemas.CRCONIntegrationConfig(
            id=self.integration_id,
            community_id=0, # Update this later
            api_url=self.api_url.value,
            api_key=self.api_key.value,
        )
        integration = CRCONIntegration(config)

        community = await submit_integration_config(interaction, integration)
        self.view.community = community
        await self.view.edit()

