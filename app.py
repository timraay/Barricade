#!/usr/bin/env python

import uvicorn

from barricade.logger import UVICORN_LOG_CONFIG, UVICORN_LOG_LEVEL

from barricade.constants import WEB_HOST, WEB_PORT
from barricade.web.app import app

if __name__ == '__main__':
    uvicorn.run(
        app,
        host=WEB_HOST,
        port=WEB_PORT,
        log_config=UVICORN_LOG_CONFIG,
        log_level=UVICORN_LOG_LEVEL,
    )
