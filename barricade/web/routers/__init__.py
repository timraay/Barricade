from fastapi import FastAPI

from . import admins
from . import auth
from . import bans
from . import communities
from . import integrations
from . import reports
from . import web_users
from . import admin_tools

__all__ = (
    "setup_all",
)

def setup_all(app: FastAPI):
    # Setup authentication routes first
    auth.setup(app)

    admins.setup(app)
    bans.setup(app)
    communities.setup(app)
    integrations.setup(app)
    reports.setup(app)
    web_users.setup(app)

    admin_tools.setup(app)
