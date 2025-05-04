import asyncio
from functools import partial
import re
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

import discord
from discord import ButtonStyle, Interaction
from discord.utils import escape_markdown as esc_md

from barricade import schemas
from barricade.constants import MAX_INTEGRATION_LIMIT
from barricade.crud.communities import get_admin_by_id, get_community_by_id
from barricade.db import models, session_factory
from barricade.discord.utils import CallableSelect, View, Modal, CallableButton, CustomException, format_url, get_danger_embed, get_success_embed, get_question_embed
from barricade.enums import IntegrationType
from barricade.exceptions import IntegrationMissingPermissionsError, IntegrationValidationError
from barricade.integrations import Integration, BattlemetricsIntegration, CRCONIntegration, INTEGRATION_TYPES
from barricade.integrations.custom import CustomIntegration
from barricade.integrations.manager import IntegrationManager
from barricade.logger import get_logger

RE_BATTLEMETRICS_ORG_URL = re.compile(r"https://www.battlemetrics.com/rcon/orgs/edit/(\d+)")
RE_CRCON_URL = re.compile(r"(http(?:s)?:\/\/(?:\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d{2,5}|.+?))(?:\/(?:(?:#|api|admin).*)?)?$")

async def configure_battlemetrics_integration(
    interaction: Interaction,
    view: 'IntegrationManagementView',
    config: schemas.BattlemetricsIntegrationConfig | None
):
    modal = ConfigureBattlemetricsIntegrationModal(view, default_values=config)
    await interaction.response.send_modal(modal)

async def configure_crcon_integration(
    interaction: Interaction,
    view: 'IntegrationManagementView',
    config: schemas.CRCONIntegrationConfig | None
):
    modal = ConfigureCRCONIntegrationModal(view, default_values=config)
    await interaction.response.send_modal(modal)

async def configure_custom_integration(
    interaction: Interaction,
    view: 'IntegrationManagementView',
    config: schemas.CustomIntegrationConfig | None
):
    modal = ConfigureCustomIntegrationModal(view, default_values=config)
    await interaction.response.send_modal(modal)

def get_config_from_community(community: models.Community, integration_id: int):
    for integration in community.integrations:
        if integration.id == integration_id:
            return integration
    raise CustomException("This integration no longer exists")


async def get_name(integration: Integration):
    try:
        return esc_md(
            await asyncio.wait_for(integration.get_instance_name(), timeout=5)
        )
    except:
        return "*Name unknown*"

class IntegrationManagementView(View):
    def __init__(self, community: schemas.Community):
        super().__init__(timeout=60*30)
        self.selected_integration_id: int | None = None
        self.community = schemas.Community.model_validate(community)
        self.integration_names: dict[int, str] = {}
        self.comments: dict[int, str] = {}
        self.logger = get_logger(self.community.id)
        self.update_integrations()
    
    async def get_integration_name(self, integration: Integration):
        if not integration.config.id:
            return await get_name(integration)
        
        if name := self.integration_names.get(integration.config.id):
            return name
        
        name = await get_name(integration)
        self.integration_names[integration.config.id] = name
        return name

    async def get_integration_hyperlink(self, integration: Integration):
        name = await self.get_integration_name(integration)
        return format_url(name, integration.get_instance_url())

    def update_integrations(self):
        """Take the current community and repopulate the list
        of integrations known to this view."""
        self.integrations: dict[int, Integration] = {}
        manager = IntegrationManager()

        for config in self.community.integrations:
            integration = manager.get_by_config(config)
            if not integration:
                self.logger.error("Integration with config %r should be registered by manager but was not" % config)
                continue

            assert integration.config.id is not None
            self.integrations[integration.config.id] = integration # type: ignore

    # --- Sending and editing

    async def get_embed_update_self(self):
        """Update this view and return the associated embed."""
        embed = discord.Embed()

        i = 0
        num_enabled = 0

        # Gather integration names in parallel, might be potentially slow if done sequentially
        integration_names = await asyncio.gather(*(
            self.get_integration_name(integration)
            for integration in self.integrations.values()
        ))

        self.clear_items()

        integration_select = CallableSelect(
            self.select_integration,
            placeholder="Select an integration...",
            options=[],
            row=0,
        )

        for i, integration in enumerate(self.integrations.values()):
            assert integration.config.id is not None
            enabled = integration.config.enabled
            name = integration_names[i]
            name_hyperlink = format_url(name, integration.get_instance_url())

            if enabled:
                emoji = "🟢"
                embed_value = f"{name_hyperlink}\n**Enabled** \\🟢"
                num_enabled += 1
            else:
                emoji = "🔴"
                embed_value = f"{name_hyperlink}\n**Disabled** \\🔴"
                if comment := self.comments.get(integration.config.id):
                    embed_value += f"\n-# {comment}"
            
            integration_select.add_option(
                label=name,
                description=f"{i+1}. {integration.meta.name}",
                value=str(integration.config.id),
                emoji=emoji,
                default=(integration.config.id == self.selected_integration_id),
            )

            embed.add_field(
                name=f"{i+1}. {integration.meta.name}",
                value=embed_value,
                inline=True
            )

        if (self.integrations):
            self.add_item(integration_select)

        integration = self.integrations.get(self.selected_integration_id) # type: ignore
        if integration:
            assert integration.config.id is not None

            if integration.config.enabled:
                self.add_item(CallableButton(
                    partial(self.disable_integration, integration.config.id),
                    style=ButtonStyle.blurple,
                    label="Disable",
                    row=1
                ))
            else:
                self.add_item(CallableButton(
                    partial(self.enable_integration, integration.config.id),
                    style=ButtonStyle.green,
                    label="Enable",
                    row=1
                ))
            
            self.add_item(CallableButton(
                partial(self.configure_integration, type(integration), integration.config.id),
                style=ButtonStyle.gray if integration.config.enabled else ButtonStyle.blurple,
                label="Reconfigure...",
                row=1
            ))

            if not integration.config.enabled:
                self.add_item(CallableButton(
                    partial(self.delete_integration, integration.config.id),
                    style=ButtonStyle.red,
                    label="Delete",
                    row=1
                ))

        self.add_item(CallableButton(
            self.add_integration,
            style=ButtonStyle.gray,
            label="Add integration...",
            row=2
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
                    assert integration.config.id is not None
                    if isinstance(validation, IntegrationValidationError):
                        await integration.disable()
                        self.comments[integration.config.id] = str(validation)
                    else:
                        self.logger.error("Failed to validate integration with ID %s" % integration.config.id, exc_info=validation)
                        self.comments[integration.config.id] = "Unexpected validation error"
            
            await self.edit()
        
        else:
            await interaction.response.defer(ephemeral=True)
            embed = await self.get_embed_update_self()
            await interaction.followup.send(embed=embed, view=self)
            self.message = await interaction.original_response()
    
    async def edit(self, interaction: Optional[Interaction] = None):
        embed = await self.get_embed_update_self()

        if interaction and not interaction.response.is_done():
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await self.message.edit(embed=embed, view=self)
    
    # --- Utilities

    async def validate_adminship(self, db: AsyncSession, user_id: int):
        db_community = await get_community_by_id(db, self.community.id)
        if not db_community:
            raise CustomException("This community no longer exists!")
        community = schemas.Community.model_validate(db_community)
        
        db_admin = await get_admin_by_id(db, user_id)
        if not db_admin or self.community.id != db_community.id or self.community.id != db_admin.community_id:
            raise CustomException("You need to be the community admin to do this!")
        
        self.community = community
        self.update_integrations()

    def get_integration(self, integration_id: int):
        integration = self.integrations.get(integration_id)
        if not integration:
            raise CustomException("This integration no longer exists")
        return integration
    
    async def validate_integration(self, integration: Integration, save_comment: bool = False):
        if save_comment and integration.config.id is None:
            raise ValueError("save_comment cannot be True if the integration hasn't been saved yet")

        try:
            missing_optional_permissions = await integration.validate(self.community)
        except IntegrationMissingPermissionsError as e:
            if save_comment:
                assert integration.config.id is not None
                self.comments[integration.config.id] = "Missing permissions"
            raise CustomException(
                "Failed to configure integration!",
                (
                    "Your API token is missing the following permissions/scopes:\n - "
                    + "\n - ".join(e.missing_permissions)
                    + "\nRefer to [the wiki](https://github.com/timraay/Barricade/wiki/Frequently-Asked-Questions#what-permissions-do-integrations-require) for a full list of required permissions."
                )
            )
        except IntegrationValidationError as e:
            if save_comment:
                assert integration.config.id is not None
                self.comments[integration.config.id] = str(e)
            raise CustomException("Failed to configure integration!", str(e))
        except Exception as e:
            if save_comment:
                assert integration.config.id is not None
                self.comments[integration.config.id] = "Unexpected validation error"
            raise CustomException("Unexpected validation error!", str(e), log_traceback=True)
        
        return missing_optional_permissions
    
    async def submit_integration_config(self, interaction: Interaction, integration: Integration):
        # Defer the interaction in case below steps take longer than 3 seconds
        await interaction.response.defer(ephemeral=True, thinking=True)

        async with session_factory.begin() as db:
            # Make sure user is admin
            await self.validate_adminship(db, interaction.user.id)

            missing_optional_permissions = await self.validate_integration(integration)
            
            # Update config in DB
            if integration.config.id:
                await integration.update(db)
            else:
                await integration.create()
                assert integration.config.id is not None
                self.community.integrations.append(
                    schemas.IntegrationConfig.model_validate(integration.config)
                )
                self.update_integrations()

            self.comments.pop(integration.config.id, None)

        if missing_optional_permissions:
            embed_desc = (
                "-# **Note:** Your API token is missing the following **optional** permissions:\n-# - "
                + "\n-# - ".join(missing_optional_permissions)
                + "\n-# These permissions might become required in the future. Refer to the wiki for more information."
            )
        else:
            embed_desc = None

        await interaction.followup.send(embed=get_success_embed(
            f"Configured {integration.meta.name} integration!",
            embed_desc
        ))
        await self.edit()


    # --- Button action handlers

    async def select_integration(self, interaction: Interaction, values: list[str]):
        integration_id = int(values[0])
        integration = self.get_integration(integration_id)
        self.selected_integration_id = integration.config.id

        await self.edit(interaction=interaction)

    async def configure_integration(self, integration_cls: type[Integration], integration_id: int | None, interaction: Interaction):
        async with session_factory() as db:
            await self.validate_adminship(db, interaction.user.id)

        if integration_id:
            integration = self.get_integration(integration_id)
            config = integration.config
        else:
            config = None

        match integration_cls.meta.type:
            case IntegrationType.BATTLEMETRICS:
                await configure_battlemetrics_integration(interaction, self, config) # type: ignore
            case IntegrationType.COMMUNITY_RCON:
                await configure_crcon_integration(interaction, self, config) # type: ignore
            case IntegrationType.CUSTOM:
                await configure_custom_integration(interaction, self, config) # type: ignore
            case _:
                self.logger.error("Tried configuring integration with unknown type %s", integration_cls.meta.type)
                raise CustomException("Unknown integration type \"%s\"" % integration_cls.meta.type)

    async def enable_integration(self, integration_id: int, interaction: Interaction):
        async with session_factory() as db:
            await self.validate_adminship(db, interaction.user.id)
            integration = self.get_integration(integration_id)
            assert integration.config.id is not None

            # Validate config
            missing_optional_permissions = await self.validate_integration(integration)
            self.comments.pop(integration.config.id, None)
            
            await integration.enable()


        embed_desc = await self.get_integration_hyperlink(integration)
        if missing_optional_permissions:
            embed_desc += (
                "\n-# **Note:** Your API token is missing the following **optional** permissions:\n-# - "
                + "\n-# - ".join(missing_optional_permissions)
                + "\n-# These permissions might become required in the future. Refer to the wiki for more information."
            )

        await interaction.response.send_message(embed=get_success_embed(
            f"Enabled {integration.meta.name} integration!", embed_desc
        ), ephemeral=True)
        await self.edit()

    async def disable_integration(self, integration_id: int, interaction: Interaction):
        async with session_factory() as db:
            await self.validate_adminship(db, interaction.user.id)
            integration = self.get_integration(integration_id)
            await integration.disable()
        
        embed = get_success_embed(
            f"Disabled {integration.meta.name} integration!",
            await self.get_integration_hyperlink(integration)
        )
        if interaction.response.is_done():
            await interaction.response.edit_message(embed=embed, view=None)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
        await self.edit()
    
    async def delete_integration(self, integration_id: int, interaction: Interaction):
        integration = self.get_integration(integration_id)

        async def confirm_delete(_interaction: Interaction):
            await _interaction.response.defer()

            async with session_factory() as db:
                await self.validate_adminship(db, interaction.user.id)

            await integration.delete()

            if config := next(
                (itg for itg in self.community.integrations if itg.id == integration_id),
                None
            ):
                self.community.integrations.remove(config)

            await _interaction.edit_original_response(
                embed=get_success_embed(f"{integration.meta.name} integration #{integration_id} deleted!"),
                view=None
            )
            self.update_integrations()
            await self.edit()
        
        view = View()
        view.add_item(
            CallableButton(confirm_delete, style=ButtonStyle.red, label="Delete Integration", single_use=True)
        )
        await interaction.response.send_message(
            embed=get_danger_embed(
                "Are you sure you want to delete this integration?",
                "This action is irreversible."
            ),
            view=view,
            ephemeral=True,
        )

    async def add_integration(self, interaction: Interaction):
        if len(self.integrations) >= MAX_INTEGRATION_LIMIT:
            raise CustomException(
                "You have reached the max number of integrations!",
                (
                    f"Only {MAX_INTEGRATION_LIMIT} integrations can be added to a community at a time."
                    " Note that you do not need to setup an integration for each individual server."
                    " Reach out to Bunker admins to request an exemption."
                )
            )
        self.clear_items()
        for integration_cls in INTEGRATION_TYPES:
            self.add_item(CallableButton(
                partial(self.configure_integration, integration_cls, None),
                style=ButtonStyle.blurple,
                label=integration_cls.meta.name,
                emoji=integration_cls.meta.emoji,
                row=0
            ))
        self.add_item(discord.ui.Button(
            style=ButtonStyle.gray,
            label="Help",
            row=1,
            url="https://github.com/timraay/Barricade/wiki/Quickstart#3-connecting-to-your-game-servers"
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
            "You are about to disconnect this integration from Barricade, meaning it will no longer"
            "receive updates from Barricade on what players to ban or unban."
            "\n\n"
            "If you want this integration to remove all its Barricade bans, press \"Yes\". If you want"
            " them to remain in place, press \"No\"."
            "\n\n"
            "This decision does not affect any other integrations. Should you choose \"Yes\", you"
            " will always be able to import your bans again in the future."
        )
    ), view=view)
    return await fut

class ConfigureBattlemetricsIntegrationModal(Modal):
    def __init__(self, view: IntegrationManagementView, default_values: Optional[schemas.BattlemetricsIntegrationConfig] = None):
        super().__init__(
            title="Configure Battlemetrics Integration",
            timeout=None
        )
        self.view = view

        # Define input fields
        self.org_url = discord.ui.TextInput(
            label="Organization URL",
            style=discord.TextStyle.short,
            placeholder="https://www.battlemetrics.com/rcon/orgs/edit/...",
        )
        self.api_key = discord.ui.TextInput(
            label="API key",
            style=discord.TextStyle.short,
        )

        # Load default values
        if default_values:
            self.integration_id = default_values.id
            self.api_key.default = default_values.api_key
            self.org_url.default = "https://www.battlemetrics.com/rcon/orgs/edit/" + str(default_values.organization_id)
        else:
            self.integration_id = None

        self.add_item(self.api_key)
        self.add_item(self.org_url)

    async def on_submit(self, interaction: Interaction):
        # Extract organization ID
        match = RE_BATTLEMETRICS_ORG_URL.match(self.org_url.value)
        if not match:
            raise CustomException("Invalid organization URL!")
        organization_id = match.group(1)

        config = schemas.BattlemetricsIntegrationConfigParams(
            id=self.integration_id,
            community_id=self.view.community.id,
            api_key=self.api_key.value,
            organization_id=organization_id,
        )
        integration = BattlemetricsIntegration(config)

        await self.view.submit_integration_config(interaction, integration)

class ConfigureCRCONIntegrationModal(Modal):
    def __init__(self, view: IntegrationManagementView, default_values: Optional[schemas.CRCONIntegrationConfig] = None):
        super().__init__(
            title="Configure Community RCON Integration",
            timeout=None
        )
        self.view = view

        # Define input fields
        self.api_url = discord.ui.TextInput(
            label="CRCON URL",
            style=discord.TextStyle.short,
        )
        self.api_key = discord.ui.TextInput(
            label="API key",
            style=discord.TextStyle.short,
        )

        # Load default values
        if default_values:
            self.integration_id = default_values.id
            self.api_url.default = default_values.api_url
            self.api_key.default = default_values.api_key
        else:
            self.integration_id = None
        
        self.add_item(self.api_url)
        self.add_item(self.api_key)

    async def on_submit(self, interaction: Interaction):
        # Validate and sanitize API URL
        match = RE_CRCON_URL.match(self.api_url.value)
        if not match:
            raise CustomException(
                "Invalid Community RCON URL!",
                "Go to any login-protected page of your CRCON and copy the URL."
            )

        config = schemas.CRCONIntegrationConfigParams(
            id=self.integration_id,
            community_id=self.view.community.id,
            api_url=match.group(1),
            api_key=self.api_key.value,
        )
        integration = CRCONIntegration(config)

        await self.view.submit_integration_config(interaction, integration)

class ConfigureCustomIntegrationModal(Modal):
    def __init__(self, view: IntegrationManagementView, default_values: Optional[schemas.CustomIntegrationConfig] = None):
        super().__init__(
            title="Configure Custom Integration",
            timeout=None
        )
        self.view = view

        # Define input fields
        self.api_url = discord.ui.TextInput(
            label="Websocket URL",
            style=discord.TextStyle.short,
            placeholder="ws://..."
        )
        self.api_key = discord.ui.TextInput(
            label="Auth Bearer Token",
            style=discord.TextStyle.short,
        )
        self.banlist_id = discord.ui.TextInput(
            label="Banlist ID",
            style=discord.TextStyle.short,
            required=False,
        )

        # Load default values
        if default_values:
            self.integration_id = default_values.id
            self.api_url.default = default_values.api_url
            self.api_key.default = default_values.api_key
            self.banlist_id.default = default_values.banlist_id
        else:
            self.integration_id = None
        
        self.add_item(self.api_url)
        self.add_item(self.api_key)
        self.add_item(self.banlist_id)

    async def on_submit(self, interaction: Interaction):
        config = schemas.CustomIntegrationConfigParams(
            id=self.integration_id,
            community_id=self.view.community.id,
            api_url=self.api_url.value,
            api_key=self.api_key.value,
            banlist_id=self.banlist_id.value or None,
        )
        integration = CustomIntegration(config)

        await self.view.submit_integration_config(interaction, integration)

