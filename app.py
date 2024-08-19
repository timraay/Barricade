#!/usr/bin/env python

import uvicorn
import logging

from barricade.constants import WEB_HOST, WEB_PORT
from barricade.web.app import app

logging.basicConfig(
    format="[%(asctime)s][%(levelname)7s][%(module)s.%(funcName)s:%(lineno)s] %(message)s",
    level=logging.INFO
)

if __name__ == '__main__':
    uvicorn.run(app, host=WEB_HOST, port=WEB_PORT)
