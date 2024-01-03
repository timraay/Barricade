import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
import logging

from bunker.db import create_tables
from bunker.discord import bot
from bunker.constants import DISCORD_BOT_TOKEN
from bunker.web import routers

@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_tables()

    try:
        await bot.login(DISCORD_BOT_TOKEN)
        asyncio.create_task(bot.connect(reconnect=True))
        await bot.wait_until_ready()
        
        logging.info("Started bot %s (ID: %s)", bot.user.name, bot.user.id)
    
        yield

    finally:
        if not bot.is_closed():
            await bot.close()

app = FastAPI(lifespan=lifespan)

# Add routers
routers.setup_all(app)
