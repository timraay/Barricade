from fastapi import FastAPI

from . import (
    admin_tools,
    admins,
    auth,
    bans,
    communities,
    integrations,
    reports,
    responses,
    web_users,
)

__all__ = ("setup_all",)


def setup_all(app: FastAPI):
    # Setup authentication routes first
    auth.setup(app)

    admins.setup(app)
    bans.setup(app)
    communities.setup(app)
    integrations.setup(app)
    reports.setup(app)
    responses.setup(app)
    web_users.setup(app)

    admin_tools.setup(app)
