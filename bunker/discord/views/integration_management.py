import asyncio
from functools import partial
import logging
from pydantic import BaseModel
import re
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, Callable, Coroutine, Any
from uuid import UUID

import discord
from discord import ButtonStyle, Interaction
from discord.utils import escape_markdown as esc_md

from bunker import schemas
from bunker.crud.communities import get_community_by_id, get_community_by_owner_id
from bunker.db import models, session_factory
from bunker.discord.utils import View, Modal, CallableButton, CustomException, format_url, get_success_embed, get_question_embed
from bunker.enums import IntegrationType
from bunker.exceptions import IntegrationValidationError
from bunker.integrations import Integration, BattlemetricsIntegration, CRCONIntegration
from bunker.integrations.manager import IntegrationManager

RE_BATTLEMETRICS_ORG_URL = re.compile(r"https://www.battlemetrics.com/rcon/orgs/edit/(\d+)")

async def configure_battlemetrics_integration(interaction: Interaction, view: 'IntegrationManagementView', config: schemas.BattlemetricsIntegrationConfig | None):
    modal = ConfigureBattlemetricsIntegrationModal(view, default_values=config)
    await interaction.response.send_modal(modal)

async def configure_crcon_integration(interaction: Interaction, view: 'IntegrationManagementView', config: schemas.CRCONIntegrationConfig | None):
    modal = ConfigureCRCONIntegrationModal(view, default_values=config)
    await interaction.response.send_modal(modal)

async def get_owned_community(db: AsyncSession, user_id: int):
    community = await get_community_by_owner_id(db, user_id)
    if not community:
        raise CustomException(
            "You need to be a community owner to do this!",
        )
    schemas.Community.model_validate(community)
    return community

def get_config_from_community(community: models.Community, integration_id: int):
    for integration in community.integrations:
        if integration.id == integration_id:
            return integration
    raise CustomException("This integration no longer exists")


async def get_name_hyperlink(integration: Integration):
    try:
        name = esc_md(await integration.get_instance_name())
    except:
        name = "*Name unknown*"

    return format_url(name, integration.get_instance_url())


class IntegrationManagementView(View):
    def __init__(self, community: models.Community):
        super().__init__(timeout=60*30)
        self.community = community
        self.update_integrations()

    def update_integrations(self):
        """Take the current community and repopulate the list
        of integrations known to this view."""
        self.integrations: dict[int, Integration] = {}
        manager = IntegrationManager()
        for db_integration in self.community.integrations:
            integration = manager.load(db_integration)
            self.integrations[integration.config.id] = integration

    # --- Sending and editing

    async def get_embed_update_self(self):
        """Update this view and return the associated embed."""
        embed = discord.Embed()
        self.clear_items()

        row = 0
        num_enabled = 0
        for row, integration in enumerate(self.integrations.values()):
            enabled = integration.config.enabled
            name = await get_name_hyperlink(integration)

            self.add_item(discord.ui.Button(
                style=ButtonStyle.green if enabled else ButtonStyle.gray,
                emoji=integration.meta.emoji,
                label=f"# {row+1}.",
                row=row,
                disabled=True
            ))

            if integration.config.enabled:
                embed.add_field(
                    name=f"{row+1}. {integration.meta.name}",
                    value=f"{name}\n**`Enabled`** \🟢",
                    inline=True
                )
            
                self.add_item(CallableButton(
                    partial(self.disable_integration, integration.config.id),
                    style=ButtonStyle.blurple,
                    label="Disable",
                    row=row
                ))

                num_enabled += 1

            else:
                embed.add_field(
                    name=f"{row+1}. {integration.meta.name}",
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
                partial(self.configure_integration, integration.config.id, integration.config.id),
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

        embed.title = f"Connected integrations ({num_enabled})"
        return embed

    async def send(self, interaction: Interaction):
        # First get how many enabled integrations there are
        to_validate = [
            integration for integration in self.integrations.values()
            if integration.config.enabled
        ]

        if to_validate:
            # Inform user some integrations will need to be validated first
            embed = discord.Embed(
                description=f"Validating {len(to_validate)} integration(s)...",
                colour=discord.Colour(0x2b2d31)
            )
            await interaction.response.send_message(embed=embed, view=self, ephemeral=True)
            self.message = await interaction.original_response()
        
            # Validate all integrations
            validations = await asyncio.gather(*[
                integration.validate(self.community)
                for integration in to_validate
            ], return_exceptions=True)

            for integration, validation in zip(to_validate, validations):
                if isinstance(validation, Exception):
                    if isinstance(validation, IntegrationValidationError):
                        async with session_factory() as db:
                            await integration.disable(db)
                    else:
                        logging.error("Failed to validate integration with ID %s" % integration.config.id, exc_info=validation)
            
            await self.edit()
        
        else:
            embed = await self.get_embed_update_self()
            await interaction.response.send_message(embed=embed, view=self, ephemeral=True)
            self.message = await interaction.original_response()
    
    async def edit(self, interaction: Optional[Interaction] = None):
        embed = await self.get_embed_update_self()

        if interaction and not interaction.response.is_done():
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await self.message.edit(embed=embed, view=self)
    
    # --- Utilities

    async def validate_ownership(self, db: AsyncSession, user_id: int):
        community = await get_community_by_id(db, self.community.id)
        if not community:
            raise CustomException("This community no longer exists!")
        
        if self.community.id != community.id or self.community.owner_id != user_id:
            raise CustomException("You need to be the community owner to do this!")
        
        self.community = community
        self.update_integrations()

    def get_integration(self, integration_id: int):
        integration = self.integrations.get(integration_id)
        if not integration:
            raise CustomException("This integration no longer exists")
        return integration
    
    async def submit_integration_config(self, interaction: Interaction, integration: Integration):
        # Defer the interaction in case below steps take longer than 3 seconds
        await interaction.response.defer(ephemeral=True, thinking=True)

        async with session_factory() as db:
            # Make sure user is owner
            await self.validate_ownership(db, interaction.user.id)

            # Validate config
            try:
                await integration.validate(self.community)
            except IntegrationValidationError as e:
                raise CustomException("Failed to configure integration!", str(e))
            
            # Update config in DB
            await integration.save_config(db)
            await db.refresh(self.community)

        await interaction.followup.send(embed=get_success_embed(
            f"Configured {integration.meta.name} integration!"
        ))

    # --- Button action handlers

    async def configure_integration(self, integration_cls: type[Integration], integration_id: int | None, interaction: Interaction):
        async with session_factory() as db:
            await self.validate_ownership(db, interaction.user.id)

        if integration_id:
            integration = self.get_integration(integration_id)
            config = integration.config
        else:
            config = None

        match integration_cls.meta.type:
            case IntegrationType.BATTLEMETRICS:
                await configure_battlemetrics_integration(interaction, self, config)
            case IntegrationType.COMMUNITY_RCON:
                await configure_crcon_integration(interaction, self, config)
            case _:
                logging.error("Tried configuring integration with unknown type %s", integration_cls.meta.type)
                raise CustomException("Unknown integration type \"%s\"" % integration_cls.meta.type)

    async def enable_integration(self, integration_id: int, interaction: Interaction):
        async with session_factory() as db:
            await self.validate_ownership(db, interaction.user.id)
            integration = self.get_integration(integration_id)

            # Validate config
            try:
                await integration.validate(self.community)
            except Exception as e:
                raise CustomException("Outdated integration configuration!", str(e), log_traceback=True)
            
            await integration.enable(db)
            await db.refresh(self.community)

        await interaction.response.send_message(embed=get_success_embed(
            f"Enabled {integration.meta.name} integration!",
            await get_name_hyperlink(integration)
        ), ephemeral=True)
        await self.edit()

    async def disable_integration(self, integration_id: int, interaction: Interaction):
        async with session_factory() as db:
            await self.validate_ownership(db, interaction.user.id)
            integration = self.get_integration(integration_id)

            remove_bans=False
            if integration.meta.ask_remove_bans:
                try:
                    remove_bans = await ask_remove_bans(interaction)
                except asyncio.TimeoutError:
                    return

            await integration.disable(db, remove_bans=remove_bans)
            await db.refresh(self.community)
        
        embed = get_success_embed(
            f"Disabled {integration.meta.name} integration!",
            await get_name_hyperlink(integration)
        )
        if interaction.response.is_done():
            await interaction.response.edit_message(embed=embed, view=None)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
        await self.edit()

    async def add_integration(self, interaction: Interaction):
        self.clear_items()
        for integration_cls in IntegrationManager.types:
            self.add_item(CallableButton(
                partial(self.configure_integration, integration_cls, None),
                style=ButtonStyle.blurple,
                label=integration_cls.meta.name,
                emoji=integration_cls.meta.emoji,
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
        self.add_item(CallableButton(partial(self.submit, True), label="Yes", style=ButtonStyle.blurple, single_use=True))
        self.add_item(CallableButton(partial(self.submit, False), label="No", style=ButtonStyle.blurple, single_use=True))

    async def submit(self, remove_bans: bool, interaction: Interaction):
        await interaction.response.defer()
        if not self.fut.done():
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
            community_id=self.view.community.id,
            api_key=self.api_key.value,
            organization_id=organization_id,
            banlist_id=banlist_id
        )
        integration = BattlemetricsIntegration(config)
        
        await self.view.submit_integration_config(interaction, integration)

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
            community_id=self.view.community.id,
            api_url=self.api_url.value,
            api_key=self.api_key.value,
        )
        integration = CRCONIntegration(config)

        await self.view.submit_integration_config(interaction, integration)

