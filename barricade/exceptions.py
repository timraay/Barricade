from barricade import schemas

class NotFoundError(Exception):
    """Raised when a requested entity does not exist"""
    pass

class AlreadyExistsError(Exception):
    """Raised when attempting to create a row or relation which
    already exists."""

class AdminNotAssociatedError(Exception):
    """Raised when attempting to transfer ownership to
    an admin not part of the community"""
    def __init__(self, admin: schemas.Admin, community: schemas.Community, *args):
        self.admin = admin
        self.community = community
        super().__init__(*args)

class AdminOwnsCommunityError(Exception):
    """Raised when attempting to remove an owner from a
    community"""
    def __init__(self, admin: schemas.Admin, *args):
        self.admin = admin
        super().__init__(*args)

class MaxLimitReachedError(Exception):
    """Raised when attempting to exceed an upper limit"""
    def __init__(self, limit: int, *args):
        self.limit = limit
        super().__init__(*args)

class InvalidPlatformError(Exception):
    """Raised when attempting to create a report token for a platform that
    the community is not known for."""


class IntegrationFailureError(Exception):
    """Generic exception raised when an integration fails to
    perform a remote action."""

class IntegrationDisabledError(IntegrationFailureError):
    """Raised when an integration is disabled when it is expected to be enabled."""

class IntegrationCommandError(IntegrationFailureError):
    """Exception when an integration utilizing the Integration protocol returns
    a response with the `failed` flag set to `true`."""
    def __init__(self, response: dict, *args: object) -> None:
        self.response = response
        super().__init__(*args)

class IntegrationValidationError(IntegrationFailureError):
    """Exception raised when an integration fails to validate"""

class IntegrationMissingPermissionsError(IntegrationValidationError):
    """Exception raised when an integration fails to validate due to a lack of permissions"""
    def __init__(self, missing_permissions: set[str], *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.missing_permissions = missing_permissions

class IntegrationBanError(IntegrationFailureError):
    """Exception raised when an integration fails to ban or
    unban a player."""
    def __init__(self, player_id: str, *args: object) -> None:
        self.player_id = player_id
        super().__init__(*args)

class IntegrationBulkBanError(IntegrationFailureError):
    """Exception raised when an integration fails to ban or
    unban one or more players during a bulk operation."""
    def __init__(self, player_ids: list[str], *args: object) -> None:
        self.player_ids = player_ids
        super().__init__(*args)

class AlreadyBannedError(IntegrationFailureError):
    """Raised when a player is already banned"""
    def __init__(self, player_id: str, *args: object) -> None:
        self.player_id = player_id
        super().__init__(*args)
