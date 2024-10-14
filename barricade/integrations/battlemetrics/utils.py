from barricade.enums import PlayerIDType

def find_player_id_in_attributes(attrs: dict):
    player_id = None
    player_id_type = PlayerIDType.STEAM_64_ID

    # Find identifier of valid type
    identifiers = attrs["identifiers"]
    for identifier_data in identifiers:
        try:
            player_id_type = PlayerIDType(identifier_data["type"])
        except KeyError:
            continue
        player_id = identifier_data["identifier"]
        break

    return player_id, player_id_type