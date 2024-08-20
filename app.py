#!/usr/bin/env python

import uvicorn
import logging

from barricade.constants import WEB_HOST, WEB_PORT, LOGS_FOLDER
from barricade.web.app import app

logging.basicConfig(
    format="[%(asctime)s][%(levelname)7s][%(module)s.%(funcName)s:%(lineno)s] %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler(filename=LOGS_FOLDER / "app.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

if __name__ == '__main__':
    uvicorn.run(app, host=WEB_HOST, port=WEB_PORT)
