import asyncio
import logging

import aiohttp
from cachetools import TTLCache

from barricade.constants import STEAM_API_KEY
from barricade.enums import PlayerIDType
from barricade.utils import get_player_id_type, safe_create_task

_steam_avatar_url_cache = TTLCache[str, asyncio.Future[str | None]](
    maxsize=1028, ttl=60 * 60 * 24
)
_steam_avatar_url_queue: set[str] = set()
_steam_avatar_url_task: asyncio.Task | None = None


async def _get_steam_avatar_urls(*steam_ids: str) -> dict[str, str]:
    if not STEAM_API_KEY:
        return {}

    async with aiohttp.ClientSession() as session:
        logging.info("Fetching Steam avatars: %s", ", ".join(steam_ids))
        response = await session.get(
            "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/",
            params={"key": STEAM_API_KEY, "steamids": ",".join(steam_ids)},
        )
        response.raise_for_status()
        data = await response.json()

        players = data.get("response", {}).get("players", [])
        if players:
            return {player["steamid"]: player["avatarfull"] for player in players}

    return {}


async def _process_steam_avatar_url_queue() -> None:
    # Wait for more Steam IDs to accumulate so that they can be batched into a single request
    await asyncio.sleep(0.1)

    steam_ids = list(_steam_avatar_url_queue)
    _steam_avatar_url_queue.clear()

    global _steam_avatar_url_task
    _steam_avatar_url_task = None

    avatar_urls: dict[str, str] = {}
    try:
        avatar_urls = await _get_steam_avatar_urls(*steam_ids)
    finally:
        for steam_id in steam_ids:
            if promise := _steam_avatar_url_cache.get(steam_id):
                promise.set_result(avatar_urls.pop(steam_id, None))


async def get_steam_avatar_url(steam_id: str) -> str | None:
    try:
        player_id_type = get_player_id_type(steam_id)
    except ValueError:
        return None

    if player_id_type != PlayerIDType.STEAM_64_ID:
        return None

    # Simply caching the result isn't enough, because multiple concurrent requests for the same
    # Steam ID would still result in multiple API calls. Instead, we cache a Future, so that all
    # concurrent requests for the same Steam ID can await the same promise.
    if promise := _steam_avatar_url_cache.get(steam_id):
        return await asyncio.shield(promise)

    promise = asyncio.get_event_loop().create_future()
    _steam_avatar_url_cache[steam_id] = promise
    _steam_avatar_url_queue.add(steam_id)

    global _steam_avatar_url_task
    if _steam_avatar_url_task is None or _steam_avatar_url_task.done():
        _steam_avatar_url_task = safe_create_task(_process_steam_avatar_url_queue())

    return await asyncio.shield(promise)
