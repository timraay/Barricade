from fastapi import FastAPI

from . import admins
from . import communities
from . import reports
from . import web_users

__all__ = (
    "setup_all",
)

def setup_all(app: FastAPI):
    # Setup authentication routes first
    web_users.setup(app)
    
    admins.setup(app)
    communities.setup(app)
    reports.setup(app)
