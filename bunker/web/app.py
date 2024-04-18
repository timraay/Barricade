import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
import logging

from bunker.db import create_tables
from bunker.discord import bot
from bunker.constants import DISCORD_BOT_TOKEN
from bunker.integrations.manager import IntegrationManager
from bunker.web import routers

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create all database tables
    await create_tables()

    # Load all integrations into the manager
    await IntegrationManager().load_all()

    try:
        # Start the Discord bot
        await bot.login(DISCORD_BOT_TOKEN)
        asyncio.create_task(bot.connect(reconnect=True))
        await bot.wait_until_ready()
        
        logging.info("Started bot %s (ID: %s)", bot.user.name, bot.user.id)
    
        # Start serving requests
        yield

    finally:
        # Close bot if necessary
        if not bot.is_closed():
            await bot.close()

app = FastAPI(lifespan=lifespan)

# Add routers
routers.setup_all(app)
