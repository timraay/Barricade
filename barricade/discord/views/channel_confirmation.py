import discord
from discord import ButtonStyle, Guild, Interaction

from barricade import schemas
from barricade.crud.communities import get_community_by_id
from barricade.db import models, session_factory
from barricade.discord.audit import audit_community_edit
from barricade.discord.crud_utils import get_admin
from barricade.discord.utils import (
    CallableButton,
    CustomException,
    View,
    get_command_mention,
    get_danger_embed,
    get_success_embed,
)
from barricade.utils import safe_create_task


async def assert_community_guild(
    db_community: models.Community, interaction: Interaction
):
    assert interaction.guild is not None
    if not db_community.guild_id:
        db_community.guild_id = interaction.guild.id
    elif db_community.guild_id != interaction.guild.id:
        raise CustomException(
            "Your community was already (partially) configured in another Discord server!",
            (
                "If you want to move to this Discord server, use the"
                f" {await get_command_mention(interaction.client.tree, 'config', 'update-guild')} command."  # type: ignore
            ),
        )
    elif db_community.hll_admin_role_id and not discord.utils.get(
        interaction.guild.roles, id=db_community.hll_admin_role_id
    ):
        # If the admin role is no longer part of the updated guild, remove it
        db_community.hll_admin_role_id = None


class UpdateGuildConfirmationView(View):
    def __init__(self, guild: Guild):
        super().__init__()
        self.guild = guild

        self.confirm_button = CallableButton(
            self.confirm, style=ButtonStyle.red, label="Confirm", single_use=True
        )
        self.add_item(self.confirm_button)

    async def send(self, interaction: Interaction):
        await interaction.response.send_message(
            embed=get_danger_embed(
                title="Do you want to move your config over to this server?",
                description=(
                    "The following settings will be reset:"
                    "\n"
                    "\n- Reports feed"
                    "\n- Confirmations feed"
                    "\n- Alerts feed"
                    "\n- Admin role"
                    "\n- Alerts role"
                ),
            ),
            view=self,
            ephemeral=True,
        )

    async def confirm(self, interaction: Interaction):
        async with session_factory.begin() as db:
            db_admin = await get_admin(db, interaction.user.id)
            assert db_admin.community is not None
            db_community = db_admin.community

            db_community.guild_id = self.guild.id
            db_community.hll_reports_channel_id = None
            db_community.hll_confirmations_channel_id = None
            db_community.hll_alerts_channel_id = None
            db_community.hll_admin_role_id = None
            db_community.hll_alerts_role_id = None
            db_community.hllv_reports_channel_id = None
            db_community.hllv_confirmations_channel_id = None
            db_community.hllv_alerts_channel_id = None
            db_community.hllv_admin_role_id = None
            db_community.hllv_alerts_role_id = None
            await db.flush()

            await interaction.response.edit_message(
                embed=get_success_embed(
                    title=f"Moved {db_admin.community.name} to the current server!"
                ),
                view=None,
            )

            community_id = db_admin.community.id

            db.expunge(db_community)
            db_community = await get_community_by_id(db, community_id)
            community = schemas.Community.model_validate(db_community)

            safe_create_task(
                audit_community_edit(
                    community=community,
                    by=interaction.user,  # type: ignore
                )
            )
