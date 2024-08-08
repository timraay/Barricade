from enum import IntFlag, auto

def _convert_name(name: str):
    return name.lower().replace("_", ".")

class Scopes(IntFlag):
    # Do NOT reorder or insert between
    STAFF = auto()
    COMMUNITY_READ = auto()
    COMMUNITY_MANAGE = auto()
    COMMUNITY_SUPERUSER = auto()
    REPORT_READ = auto()
    REPORT_MANAGE = auto()
    REPORT_SUPERUSER = auto()

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
            _convert_name(v.name)
            for v in self
        )
    
    def to_dict(self) -> dict[str, str | None]:
        return {
            _convert_name(v.name): SCOPE_DESCRIPTIONS.get(v)
            for v in self
        }

SCOPE_DESCRIPTIONS = {
    Scopes.STAFF: "Manage web users",
    Scopes.COMMUNITY_READ: "See all communities and their admins",
    Scopes.COMMUNITY_MANAGE: "Manage your own community",
    Scopes.COMMUNITY_SUPERUSER: "Manage all communities and create new ones",
    Scopes.REPORT_READ: "See all reports",
    Scopes.REPORT_MANAGE: "Manage your own reports",
    Scopes.REPORT_SUPERUSER: "Delete reports",
}
