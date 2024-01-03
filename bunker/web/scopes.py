from enum import IntFlag, auto

def _convert_name(name: str):
    return name.lower().replace("_", ".")

class Scopes(IntFlag):
    COMMUNITY = auto()
    COMMUNITY_CREATE = auto()
    REPORT_DELETE = auto()
    STAFF = auto()

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
    Scopes.COMMUNITY: "Manage communities and their admins",
    Scopes.COMMUNITY_CREATE: "Create new communities",
    Scopes.REPORT_DELETE: "Delete reports",
    Scopes.STAFF: "Manage web users",
}
