#!/usr/bin/env python

import uvicorn

import barricade.logger # Configure logging

from barricade.constants import WEB_HOST, WEB_PORT
from barricade.web.app import app

if __name__ == '__main__':
    uvicorn.run(app, host=WEB_HOST, port=WEB_PORT)
