from enum import IntFlag, auto

def _convert_name(name: str):
    return name.lower().replace("_", ".")

class Scopes(IntFlag):
    # Do NOT reorder or insert between
    STAFF = auto()
    COMMUNITY_ME_READ = auto()
    COMMUNITY_ME_MANAGE = auto()
    COMMUNITY_READ = auto()
    COMMUNITY_MANAGE = auto()
    REPORT_ME_READ = auto()
    REPORT_ME_MANAGE = auto()
    REPORT_READ = auto()
    REPORT_MANAGE = auto()
    BAN_ME_READ = auto()
    BAN_ME_MANAGE = auto()
    BAN_READ = auto()
    BAN_MANAGE = auto()

    @classmethod
    def all(cls):
        return cls(~0)

    @classmethod
    def from_list(cls, list_: list[str]):
        self = cls(0)
        for value in list_:
            key = value.upper().replace(".", "_")
            self |= cls[key]
        return self

    def to_list(self) -> list[str]:
        return list(
            _convert_name(v.name) # type: ignore
            for v in self
        )
    
    def to_dict(self) -> dict[str, str | None]:
        return {
            _convert_name(v.name): SCOPE_DESCRIPTIONS.get(v)  # type: ignore
            for v in self
        }

SCOPE_DESCRIPTIONS = {
    Scopes.STAFF: "Manage web users",
    Scopes.COMMUNITY_ME_READ: "Retrieve information about your community",
    Scopes.COMMUNITY_ME_MANAGE: "Make changes to your community",
    Scopes.COMMUNITY_READ: "Retrieve all communities",
    Scopes.COMMUNITY_MANAGE: "Manage all communities",
    Scopes.REPORT_ME_READ: "Retrieve all reports made by your community",
    Scopes.REPORT_ME_MANAGE: "Edit and delete reports made by your community",
    Scopes.REPORT_READ: "Retrieve all reports",
    Scopes.REPORT_MANAGE: "Manage all reports",
    Scopes.BAN_ME_READ: "Retrieve all bans made by your community",
    Scopes.BAN_ME_MANAGE: "Create and revoke bans for your community",
    Scopes.BAN_READ: "Retrieve all bans",
    Scopes.BAN_MANAGE: "Manage all bans",
}
