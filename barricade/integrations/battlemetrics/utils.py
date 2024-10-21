from barricade.enums import PlayerIDType

def find_player_id_in_attributes(attrs: dict) -> tuple[str | None, PlayerIDType]:
    player_id: str | None = None
    player_id_type = PlayerIDType.STEAM_64_ID

    # Find identifier of valid type
    identifiers = attrs["identifiers"]
    for identifier_data in identifiers:
        try:
            player_id_type = PlayerIDType(identifier_data["type"])
        except ValueError:
            continue
        player_id = identifier_data["identifier"]
        break

    if player_id and player_id.startswith("miHash:"):
        player_id = None

    return player_id, player_id_type

class Scope:
    def __init__(self, *parts: str, flexible: bool = False) -> None:
        self.parts = parts
        self.dynamic_part_indices = [
            i for i, p in enumerate(parts)
            if p.startswith("{") and p.endswith("}")
        ]
        self.flexible = flexible

        if self.dynamic_part_indices and self.flexible:
            raise ValueError("Scope cannot both have dynamic parts and be flexible")

    def __str__(self) -> str:
        return ":".join(self.parts)
    
    def __repr__(self) -> str:
        return "<" + self.__str__() + ">"
    
    def __len__(self):
        return len(self.parts)
    
    def __hash__(self) -> int:
        return self.__str__().__hash__()
    
    def __eq__(self, value: object) -> bool:
        if isinstance(value, Scope):
            return self.parts == value.parts
        return NotImplemented
    
    def _resolve_dynamic_parts(self, params: dict[str, str]):
        parts = list(self.parts)
        if self.dynamic_part_indices:
            for i in self.dynamic_part_indices:
                parts[i] = parts[i].format(**params)
        return parts

    @classmethod
    def from_string(cls, s: str, *, flexible: bool = False):
        return cls(*s.split(":"), flexible=flexible)
    
    def to_string(self, params: dict[str, str] | None = None):
        if not params:
            return self.__str__()
        
        parts = self._resolve_dynamic_parts(params)
        return ":".join(parts)
    
    def covers(self, other: 'Scope', params: dict[str, str]):
        if len(other.parts) < len(self.parts) and not other.flexible:
            return False
        
        other_parts = other._resolve_dynamic_parts(params)

        return all(
            other_part == self_part
            for other_part, self_part
            in zip(other_parts, self.parts)
        )
        # for i, (self_part, other_part) in enumerate(zip(self_parts[:len(other)], other.parts)):
        #     print(i, self_part, other_part, i in self.dynamic_part_indices, self_part != other_part)
        #     if self_part != other_part:
        #         return False
        
        # return True
