from discord import Embed, Interaction, app_commands
from discord.ext import commands

from barricade import schemas
from barricade.crud.communities import get_community_by_id
from barricade.db import session_factory
from barricade.discord.autocomplete import atcp_integration_enabled
from barricade.discord.bot import Bot
from barricade.discord.utils import CustomException, get_error_embed_from_exc, get_success_embed
from barricade.discord.views.channel_confirmation import get_admin
from barricade.integrations.manager import IntegrationManager

class IntegrationsCog(commands.Cog):
    @app_commands.command(name="repopulate-integration", description="Upload any missing bans to an integration")
    @app_commands.autocomplete(
        integration_id=atcp_integration_enabled,
    )
    @app_commands.rename(
        integration_id="integration",
    )
    async def repopulate_integration(self, interaction: Interaction, integration_id: str):
        im = IntegrationManager()
        integration = im.get_by_id(int(integration_id))
        if integration is None:
            raise CustomException("Integration not found!")
        
        async with session_factory() as db:
            db_admin = await get_admin(db, interaction.user.id)
            assert db_admin.community is not None

            community_id = db_admin.community.id
            
            db.expire(db_admin)
            db_community = await get_community_by_id(db, community_id)
            community = schemas.Community.model_validate(db_community)

        await interaction.response.defer(ephemeral=True)
        try:
            await integration.validate(community)
        except Exception as e:
            raise CustomException("Failed to validate integration!", str(e))
        
        await interaction.followup.send(
            embed=Embed(description="Repopulating ban list. This might take a while.")
        )

        message = await interaction.original_response()

        try:
            num_success, num_total = await integration.repopulate()
        except Exception as e:
            embed = get_error_embed_from_exc(e)
        else:
            embed = get_success_embed(
                title="Repopulated ban list!",
                description=f"Submitted {num_success}/{num_total} new bans to your {integration.meta.name} integration."
            )

        await message.edit(embed=embed)        

async def setup(bot: 'Bot'):
    await bot.add_cog(IntegrationsCog(bot))
