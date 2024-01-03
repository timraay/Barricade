#!/usr/bin/env python

import uvicorn
import asyncio

from bunker.constants import DISCORD_BOT_TOKEN
from bunker.web import app

def pre_flight():
    if not DISCORD_BOT_TOKEN:
        raise Exception("DISCORD_BOT_TOKEN not set")

if __name__ == '__main__':
    pre_flight()
    uvicorn.run(app, host="127.0.0.1", port=5050)
