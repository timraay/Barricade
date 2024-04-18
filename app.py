#!/usr/bin/env python

import uvicorn
import logging

from bunker.constants import DISCORD_BOT_TOKEN, DISCORD_GUILD_ID
from bunker.web.app import app

logging.basicConfig(
    format="[%(asctime)s][%(levelname)7s][%(module)s.%(funcName)s:%(lineno)s] %(message)s",
    level=logging.INFO
)

def pre_flight():
    if not DISCORD_BOT_TOKEN:
        raise Exception("DISCORD_BOT_TOKEN not set")

if __name__ == '__main__':
    pre_flight()
    uvicorn.run(app, host="127.0.0.1", port=5050)
