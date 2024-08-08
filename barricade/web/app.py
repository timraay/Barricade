import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
import logging

from barricade import integrations
from barricade.db import create_tables
from barricade.discord import bot
from barricade.constants import DISCORD_BOT_TOKEN, WEB_DOCS_VISIBLE
from barricade.web import routers

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create all database tables
    await create_tables()

    # Load all integrations into the manager
    await integrations.load_all()

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

if WEB_DOCS_VISIBLE:
    app = FastAPI(lifespan=lifespan)
else:
    # Disable automatically generated documentation
    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)

# Add routers
routers.setup_all(app)
