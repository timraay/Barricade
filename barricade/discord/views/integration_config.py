import asyncio
import re
from typing import Generic, TypeVar, assert_never

import discord
from discord.utils import escape_markdown as esc_md
from sqlalchemy.ext.asyncio import AsyncSession

from barricade import schemas
from barricade.config import ConfigOptionCategory
from barricade.db import session_factory
from barricade.discord.communities import (
    assert_has_any_admin_role,
)
from barricade.discord.crud_utils import get_community
from barricade.discord.utils import CustomException, Modal, handle_error_wrap
from barricade.discord.views.community_config import CommunityConfigView
from barricade.enums import Emojis, IntegrationType
from barricade.exceptions import (
    IntegrationMissingPermissionsError,
    IntegrationValidationError,
)
from barricade.integrations.battlemetrics.integration import BattlemetricsIntegration
from barricade.integrations.bifrost.integration import BifrostIntegration
from barricade.integrations.crcon.integration import CRCONIntegration
from barricade.integrations.custom.integration import CustomIntegration
from barricade.integrations.integration import Integration
from barricade.integrations.manager import IntegrationManager
from barricade.logger import get_logger
from barricade.utils import validate_url


async def safe_get_integration_name(integration: Integration) -> str | None:
    """Get the name of an integration, returning a placeholder if it fails."""
    try:
        return await integration.get_instance_name()
    except Exception:
        return None


async def validate_integration(
    integration: Integration, community: schemas.Community
) -> set[str]:
    """Validate an integration, raising a CustomException if it fails."""
    try:
        missing_optional_permissions = await integration.validate(community)
    except IntegrationMissingPermissionsError as e:
        # if save_comment:
        #     assert integration.config.id is not None
        #     self.comments[integration.config.id] = "Missing permissions"
        raise CustomException(
            "Failed to configure integration!",
            (
                "Your API token is missing the following permissions/scopes:\n - "
                + "\n - ".join(e.missing_permissions)
                + "\nRefer to [the wiki](https://github.com/timraay/Barricade/wiki/Frequently-Asked-Questions#what-permissions-do-integrations-require) for a full list of required permissions."
            ),
        ) from None
    except IntegrationValidationError as e:
        # if save_comment:
        #     assert integration.config.id is not None
        #     self.comments[integration.config.id] = str(e)
        raise CustomException("Failed to configure integration!", str(e)) from None
    except Exception as e:
        # if save_comment:
        #     assert integration.config.id is not None
        #     self.comments[integration.config.id] = "Unexpected validation error"
        raise CustomException(
            "Unexpected validation error!", str(e), log_traceback=True
        ) from None

    return missing_optional_permissions


class IntegrationConfigButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"community:(?P<community_id>\d+):integration:(?P<integration_id>\d+):config:(?P<command>\w+)",
):
    def __init__(
        self,
        button: discord.ui.Button,
        community_id: int,
        integration_id: int,
        command: str,
    ):
        self.community_id = community_id
        self.integration_id = integration_id
        self.command = command

        button.custom_id = (
            f"community:{community_id}:integration:{integration_id}:config:{command}"
        )
        super().__init__(button)

    @classmethod
    async def from_custom_id(  # type: ignore
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Button,
        match: re.Match[str],
        /,
    ):
        return cls(
            button=item,
            community_id=int(match["community_id"]),
            integration_id=int(match["integration_id"]),
            command=match["command"],
        )

    @handle_error_wrap
    async def callback(self, interaction: discord.Interaction):
        async with session_factory.begin() as db:
            community = await self.get_community(db)
            assert isinstance(interaction.user, discord.Member)
            assert_has_any_admin_role(interaction.user, community)

            match self.command:
                case "enable":
                    await self.handle_enable_command(db, interaction)
                case "edit":
                    await self.handle_edit_command(db, interaction)
                case "disable":
                    await self.handle_disable_command(db, interaction)
                case "delete":
                    await self.handle_delete_command(db, interaction)
                case "expand":
                    await self.handle_expand_command(db, interaction)
                case _:
                    raise ValueError(f"Unknown command: {self.command}")

    async def get_community(self, db: AsyncSession) -> schemas.Community:
        """Get the community associated with this button."""
        db_community = await get_community(db, self.community_id)
        return schemas.Community.model_validate(db_community)

    def get_integration(self) -> Integration:
        """Get the integration associated with this button."""
        manager = IntegrationManager()
        integration = manager.get_by_id(self.integration_id)
        if not integration:
            raise CustomException(
                "Integration no longer exists",
            )
        return integration

    async def handle_enable_command(
        self,
        db: AsyncSession,
        interaction: discord.Interaction,
    ):
        """Handle the enable command for an integration."""
        integration = self.get_integration()

        if integration.config.enabled:
            raise CustomException("Integration is already enabled")

        community = await self.get_community(db)
        await interaction.response.defer(ephemeral=True)
        await validate_integration(integration, community)

        await integration.enable()

        # Expire and fetch again to ensure we have the latest available config
        db.expire_all()
        community = await self.get_community(db)
        view = IntegrationConfigView(
            community, expanded_integration_id=self.integration_id
        )
        await view.prepare()
        await interaction.edit_original_response(view=view)

    async def handle_edit_command(
        self,
        db: AsyncSession,
        interaction: discord.Interaction,
    ):
        """Handle the edit command for an integration."""
        integration = self.get_integration()
        modal_cls = get_integration_edit_modal_class(integration.meta.type)
        modal = modal_cls.from_integration(integration)
        await interaction.response.send_modal(modal)

    async def handle_disable_command(
        self,
        db: AsyncSession,
        interaction: discord.Interaction,
    ):
        """Handle the disable command for an integration."""
        integration = self.get_integration()

        if not integration.config.enabled:
            raise CustomException("Integration is already disabled")

        await integration.disable()

        community = await self.get_community(db)
        view = IntegrationConfigView(
            community, expanded_integration_id=self.integration_id
        )
        await view.prepare()
        await interaction.response.edit_message(view=view)

    async def handle_delete_command(
        self,
        db: AsyncSession,
        interaction: discord.Interaction,
    ):
        """Handle the delete command for an integration."""
        integration = self.get_integration()

        if integration.config.enabled:
            raise CustomException(
                "Integration must be disabled before it can be deleted"
            )

        await integration.delete()

        db.expire_all()
        community = await self.get_community(db)

        view = IntegrationConfigView(community)
        await view.prepare()
        await interaction.response.edit_message(view=view)

    async def handle_expand_command(
        self,
        db: AsyncSession,
        interaction: discord.Interaction,
    ):
        """Handle the expand command for an integration."""
        community = await self.get_community(db)

        view = IntegrationConfigView(
            community, expanded_integration_id=self.integration_id
        )
        await view.prepare()
        await interaction.response.edit_message(view=view)


class IntegrationAddSelect(
    discord.ui.DynamicItem[discord.ui.Select],
    template=r"community:(?P<community_id>\d+):integration:add",
):
    options: list[discord.SelectOption] = [
        discord.SelectOption(
            label=integration.meta.name,
            value=integration_type.name,
            emoji=integration.meta.emoji,
        )
        for (integration_type, integration) in (
            (IntegrationType.COMMUNITY_RCON, CRCONIntegration),
            (IntegrationType.BATTLEMETRICS, BattlemetricsIntegration),
            (IntegrationType.CUSTOM, CustomIntegration),
        )
    ]

    def __init__(
        self,
        community_id: int,
        select: discord.ui.Select | None = None,
    ):
        self.community_id = community_id

        if not select:
            select = discord.ui.Select(
                placeholder="Add a new integration...",
                options=self.options,
            )

        select.custom_id = f"community:{community_id}:integration:add"
        super().__init__(select)

    @classmethod
    async def from_custom_id(  # type: ignore
        cls,
        interaction: discord.Interaction,
        item: discord.ui.Select,
        match: re.Match[str],
        /,
    ):
        return cls(
            community_id=int(match["community_id"]),
            select=item,
        )

    @handle_error_wrap
    async def callback(self, interaction: discord.Interaction):
        # Assert that user is still an admin
        # async with session_factory() as db:
        #     community = await get_community(db, self.community_id)
        #     assert isinstance(interaction.user, discord.Member)
        #     assert_has_admin_role(interaction.user, community)

        integration_type = IntegrationType[self.item.values[0]]
        modal_cls = get_integration_edit_modal_class(integration_type)
        modal = modal_cls.new(self.community_id)
        await interaction.response.send_modal(modal)


class IntegrationConfigView(CommunityConfigView):
    def __init__(
        self,
        community: schemas.Community,
        *,
        expanded_integration_id: int = -1,
    ):
        self.integrations: dict[int, Integration] = {}
        self.integration_names: dict[int, str] = {}
        self.expanded_integration_id = expanded_integration_id

        super().__init__(community, ConfigOptionCategory.INTEGRATIONS)

        self.logger = get_logger(self.community.id)

    @property
    def do_collapse(self) -> bool:
        return len(self.integrations) >= 3

    def update_integrations(self):
        """Take the current community and repopulate the list
        of integrations known to this view."""
        self.integrations.clear()
        manager = IntegrationManager()

        for config in self.community.integrations:
            integration = manager.get_by_config(config)
            if not integration:
                self.logger.error(
                    "Integration with config %r should be registered by manager but was not",
                    config,
                )
                continue

            assert integration.config.id is not None
            self.integrations[integration.config.id] = integration  # type: ignore

    async def _update_integration_names(self) -> None:
        """Update the integration names for the current community."""
        self.integration_names = {
            integration_id: integration_name
            for integration_id, integration_name in zip(
                self.integrations,
                await asyncio.gather(
                    *[
                        safe_get_integration_name(integration)
                        for integration in self.integrations.values()
                    ]
                ),
                strict=True,
            )
            if integration_name is not None
        }

    async def prepare(self) -> None:
        self.update_integrations()
        await self._update_integration_names()
        self.setup_view()

    def _get_main_container(self) -> discord.ui.Container:
        container = super()._get_main_container()

        for integration in self.integrations.values():
            container.add_item(
                discord.ui.Separator(
                    visible=True, spacing=discord.SeparatorSpacing.large
                )
            )

            do_collapse = (
                self.do_collapse
                and integration.config.id != self.expanded_integration_id
            )

            # Integration header & title
            assert integration.config.id is not None
            integration_name = self.integration_names.get(integration.config.id, "")

            status_text = "🟢 Enabled" if integration.config.enabled else "🔴 Disabled"
            title_text = f"{integration.meta.emoji} **{esc_md(integration_name) or 'Unnamed integration'}**"

            if do_collapse:
                integration_title = f"`{status_text}`  {title_text}"

                container.add_item(
                    discord.ui.Section(
                        integration_title,
                        accessory=IntegrationConfigButton(
                            button=discord.ui.Button(
                                label="Expand  ▼",
                                style=discord.ButtonStyle.gray,
                            ),
                            community_id=self.community.id,
                            integration_id=integration.config.id,
                            command="expand",
                        ),  # type: ignore
                    )
                )

            else:
                integration_title = f"`{status_text}`\n## {title_text}"

                # Get and validate URL to public integration page
                integration_url = integration.get_instance_url()
                if integration_url:
                    try:
                        integration_url = validate_url(integration_url)
                    except ValueError:
                        integration_url = None

                # Add integration title and URL button (if available)
                if integration_url:
                    container.add_item(
                        discord.ui.Section(
                            integration_title,
                            accessory=discord.ui.Button(
                                label="Open",
                                url=integration_url,
                                style=discord.ButtonStyle.gray,
                            ),
                        )
                    )
                else:
                    container.add_item(discord.ui.TextDisplay(integration_title))

                # Error messages
                error_messages: list[str] = []
                if not integration_name:
                    error_messages.append("Could not resolve integration name")

                if error_messages:
                    for error_message in error_messages:
                        container.add_item(
                            discord.ui.TextDisplay(
                                f"{Emojis.HIGHLIGHT_RED} {error_message}"
                            )
                        )

                    container.add_item(
                        discord.ui.Separator(
                            visible=False, spacing=discord.SeparatorSpacing.small
                        )
                    )

                # Button row
                action_row = discord.ui.ActionRow()
                action_row.add_item(
                    IntegrationConfigButton(
                        button=discord.ui.Button(
                            label="Enable",
                            style=discord.ButtonStyle.green,
                            disabled=integration.config.enabled,
                        ),
                        community_id=self.community.id,
                        integration_id=integration.config.id,
                        command="enable",
                    )
                )
                action_row.add_item(
                    IntegrationConfigButton(
                        button=discord.ui.Button(
                            label="Edit",
                            style=discord.ButtonStyle.blurple,
                        ),
                        community_id=self.community.id,
                        integration_id=integration.config.id,
                        command="edit",
                    )
                )
                action_row.add_item(
                    IntegrationConfigButton(
                        button=discord.ui.Button(
                            label="Disable",
                            style=discord.ButtonStyle.red,
                            disabled=not integration.config.enabled,
                        ),
                        community_id=self.community.id,
                        integration_id=integration.config.id,
                        command="disable",
                    )
                )

                if not integration.config.enabled:
                    action_row.add_item(
                        IntegrationConfigButton(
                            button=discord.ui.Button(
                                label="Delete",
                                style=discord.ButtonStyle.red,
                            ),
                            community_id=self.community.id,
                            integration_id=integration.config.id,
                            command="delete",
                        )
                    )

                container.add_item(action_row)

        container.add_item(
            discord.ui.Separator(visible=True, spacing=discord.SeparatorSpacing.large)
        )

        container.add_item(
            discord.ui.ActionRow(
                IntegrationAddSelect(
                    community_id=self.community.id,
                )  # type: ignore
            )
        )

        container.add_item(
            discord.ui.TextDisplay(
                "-# **Note:** Multiple servers do __not__ require multiple integrations."
                " See the [FAQ](https://github.com/timraay/Barricade/wiki/Frequently-Asked-Questions#i-have-multiple-servers-do-i-need-to-add-multiple-integrations)."
            )
        )

        return container


IntegrationT = TypeVar("IntegrationT", bound=Integration)


class _IntegrationEditModal(
    Generic[IntegrationT], Modal, title="Configure Integration"
):
    def __init__(
        self,
        community_id: int,
        integration_id: int | None,
        default_values: schemas.IntegrationConfigParams | None = None,
    ):
        super().__init__(timeout=None)
        self.community_id = community_id
        self.integration_id = integration_id
        self.setup_fields(default_values)

    @classmethod
    def from_integration(cls, integration: IntegrationT):
        return cls(
            community_id=integration.config.community_id,
            integration_id=integration.config.id,
            default_values=integration.config,
        )

    @classmethod
    def new(cls, community_id: int):
        return cls(community_id, integration_id=None, default_values=None)

    async def get_community(self, db: AsyncSession) -> schemas.Community:
        """Get the community associated with this modal."""
        db_community = await get_community(db, self.community_id)
        return schemas.Community.model_validate(db_community)

    def setup_fields(self, default_values: schemas.IntegrationConfigParams | None):
        raise NotImplementedError

    def apply_values_to_config(self, config: schemas.IntegrationConfigParams) -> None:
        """Edit the given config using the modal's input values."""
        raise NotImplementedError

    def create_new_config(self) -> schemas.IntegrationConfigParams:
        """Create a new integration config from the modal's input values."""
        raise NotImplementedError

    def create_new_integration(
        self, params: schemas.IntegrationConfigParams
    ) -> IntegrationT:
        """Create an Integration object from the modal's input values."""
        raise NotImplementedError

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # Find existing integration if it exists
        manager = IntegrationManager()
        integration: Integration | None = None
        if self.integration_id is not None:
            integration = manager.get_by_id(self.integration_id)
            if not integration:
                raise CustomException("Integration no longer exists")

        # Create a new config
        new_config = (
            integration.config.model_copy() if integration else self.create_new_config()
        )
        self.apply_values_to_config(new_config)

        async with session_factory.begin() as db:
            # Create a temporary integration to validate the config without affecting any
            # existing integrations.
            temp_integration = self.create_new_integration(new_config)
            assert temp_integration.config.id == self.integration_id
            assert temp_integration.config.community_id == self.community_id

            community = await self.get_community(db)

            await interaction.response.defer(ephemeral=True)
            await validate_integration(temp_integration, community)

            # If a new integration is being created
            if integration is None:
                # Create new integration
                await temp_integration.create()
                integration = temp_integration

            # If an existing integration is being edited
            else:
                # Update config of existing integration
                integration.replace_config(new_config)
                await temp_integration.update(db)

        # Refresh the view
        async with session_factory() as db:
            community = await self.get_community(db)
            view = IntegrationConfigView(
                community, expanded_integration_id=integration.config.id or -1
            )
            await view.prepare()
            await interaction.edit_original_response(view=view)


class BattlemetricsIntegrationEditModal(
    _IntegrationEditModal[BattlemetricsIntegration],
    title="Configure Battlemetrics Integration",
):
    RE_ORG_URL = re.compile(r"https://www.battlemetrics.com/rcon/orgs/edit/(\d+)")

    def setup_fields(self, default_values: schemas.IntegrationConfigParams | None):
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
            self.api_key.default = default_values.api_key
            self.org_url.default = (
                "https://www.battlemetrics.com/rcon/orgs/edit/"
                + str(default_values.organization_id)
            )

        self.add_item(self.api_key)
        self.add_item(self.org_url)

    def apply_values_to_config(self, config: schemas.IntegrationConfigParams) -> None:
        # Extract organization ID
        match = self.RE_ORG_URL.match(self.org_url.value)
        if not match:
            raise CustomException("Invalid organization URL!")
        organization_id = match.group(1)

        config.organization_id = organization_id
        config.api_key = self.api_key.value

    def create_new_config(self) -> schemas.BattlemetricsIntegrationConfigParams:
        return schemas.BattlemetricsIntegrationConfigParams(
            community_id=self.community_id,
            organization_id=None,
            api_key="",
        )

    def create_new_integration(
        self, params: schemas.IntegrationConfigParams
    ) -> BattlemetricsIntegration:
        return BattlemetricsIntegration(
            schemas.BattlemetricsIntegrationConfigParams.model_validate(params)
        )


class CRCONIntegrationEditModal(
    _IntegrationEditModal[CRCONIntegration],
    title="Configure CRCON Integration",
):
    RE_API_URL = re.compile(
        r"(http(?:s)?:\/\/(?:\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d{2,5}|.+?))(?:\/(?:(?:#|api|admin).*)?)?$"
    )

    def setup_fields(self, default_values: schemas.IntegrationConfigParams | None):
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
            self.api_url.default = default_values.api_url
            self.api_key.default = default_values.api_key

        self.add_item(self.api_key)
        self.add_item(self.api_url)

    def apply_values_to_config(self, config: schemas.IntegrationConfigParams) -> None:
        # Extract API URL
        match = self.RE_API_URL.match(self.api_url.value)
        if not match:
            raise CustomException(
                "Invalid Community RCON URL!",
                "Go to any login-protected page of your CRCON and copy the URL.",
            )
        api_url = match.group(1)

        config.api_url = api_url
        config.api_key = self.api_key.value

    def create_new_config(self) -> schemas.CRCONIntegrationConfigParams:
        return schemas.CRCONIntegrationConfigParams(
            community_id=self.community_id,
            api_url="",
            api_key="",
        )

    def create_new_integration(
        self, params: schemas.IntegrationConfigParams
    ) -> CRCONIntegration:
        return CRCONIntegration(
            schemas.CRCONIntegrationConfigParams.model_validate(params)
        )


class BifrostIntegrationEditModal(
    _IntegrationEditModal[BifrostIntegration],
    title="Configure Bifrost Integration",
):
    def setup_fields(self, default_values: schemas.IntegrationConfigParams | None):
        # Define input fields
        self.api_key = discord.ui.TextInput(
            label="Access token",
            style=discord.TextStyle.short,
        )

        # Load default values
        if default_values:
            self.api_key.default = default_values.api_key

        self.add_item(self.api_key)

    def apply_values_to_config(self, config: schemas.IntegrationConfigParams) -> None:
        config.api_key = self.api_key.value

    def create_new_config(self) -> schemas.BifrostIntegrationConfigParams:
        return schemas.BifrostIntegrationConfigParams(
            community_id=self.community_id,
            api_key="",
        )

    def create_new_integration(
        self, params: schemas.IntegrationConfigParams
    ) -> BifrostIntegration:
        return BifrostIntegration(
            schemas.BifrostIntegrationConfigParams.model_validate(params)
        )


class CustomIntegrationEditModal(
    _IntegrationEditModal[CustomIntegration],
    title="Configure Custom Integration",
):
    def setup_fields(self, default_values: schemas.IntegrationConfigParams | None):
        # Define input fields
        self.api_url = discord.ui.TextInput(
            label="Websocket URL", style=discord.TextStyle.short, placeholder="ws://..."
        )
        self.api_key = discord.ui.TextInput(
            label="Auth Bearer Token",
            style=discord.TextStyle.short,
        )
        self.hll_banlist_id = discord.ui.TextInput(
            label="HLL (WW2) Banlist ID",
            style=discord.TextStyle.short,
            required=False,
        )
        self.hllv_banlist_id = discord.ui.TextInput(
            label="HLL: Vietnam Banlist ID",
            style=discord.TextStyle.short,
            required=False,
        )

        # Load default values
        if default_values:
            self.api_url.default = default_values.api_url
            self.api_key.default = default_values.api_key
            self.hll_banlist_id.default = default_values.hll_banlist_id
            self.hllv_banlist_id.default = default_values.hllv_banlist_id

        self.add_item(self.api_key)
        self.add_item(self.api_url)
        self.add_item(self.hll_banlist_id)
        self.add_item(self.hllv_banlist_id)

    def apply_values_to_config(self, config: schemas.IntegrationConfigParams) -> None:
        config.api_url = self.api_url.value
        config.api_key = self.api_key.value
        config.hll_banlist_id = self.hll_banlist_id.value or None
        config.hllv_banlist_id = self.hllv_banlist_id.value or None

    def create_new_config(self) -> schemas.CustomIntegrationConfigParams:
        return schemas.CustomIntegrationConfigParams(
            community_id=self.community_id,
            api_url="",
            api_key="",
        )

    def create_new_integration(
        self, params: schemas.IntegrationConfigParams
    ) -> CustomIntegration:
        return CustomIntegration(
            schemas.CustomIntegrationConfigParams.model_validate(params)
        )


def get_integration_edit_modal_class(
    integration_type: IntegrationType,
) -> type[_IntegrationEditModal]:
    """Get the appropriate IntegrationEditModal class for a given integration type."""
    match integration_type:
        case IntegrationType.BATTLEMETRICS:
            return BattlemetricsIntegrationEditModal
        case IntegrationType.COMMUNITY_RCON:
            return CRCONIntegrationEditModal
        case IntegrationType.BIFROST:
            return BifrostIntegrationEditModal
        case IntegrationType.CUSTOM:
            return CustomIntegrationEditModal
        case _:
            assert_never(integration_type)
            raise ValueError(f"Unknown integration type: {integration_type}")
