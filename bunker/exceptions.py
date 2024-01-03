from bunker import schemas

class NotFoundError(Exception):
    """Raised when a requested entity does not exist"""
    pass

class AdminNotAssociatedError(Exception):
    """Raised when attempting to transfer ownership to
    an admin not part of the community"""
    def __init__(self, admin: schemas.Admin, community: schemas.Community, *args):
        self.admin = admin
        self.community = community
        super().__init__(*args)

class AdminAlreadyAssociatedError(Exception):
    """Raised when attempting to add an admin to more than
    one community"""
    def __init__(self, *args):
        super().__init__(*args)

class AdminOwnsCommunityError(Exception):
    """Raised when attempting to remove an owner from a
    community"""
    def __init__(self, admin: schemas.Admin, *args):
        self.admin = admin
        super().__init__(*args)

class TooManyAdminsError(Exception):
    """Raised when attempting to exceed the upper limit of
    admins each community is allowed to have"""
    def __init__(self, *args):
        super().__init__(*args)
