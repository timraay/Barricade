import asyncio
import contextlib
import logging
import re
import urllib.parse
from collections.abc import Coroutine, Iterable, Sequence
from functools import wraps
from typing import TypeVar, assert_never

from cachetools import TTLCache
from cachetools.keys import hashkey

from barricade.enums import Game, PlayerIDType


def async_ttl_cache(size: int, seconds: int):
    def decorator(func):
        func.cache = TTLCache(size, ttl=seconds)

        @wraps(func)
        async def wrapper(*args, **kwargs):
            k = hashkey(*args, **kwargs)
            try:
                return func.cache[k]
            except KeyError:
                pass  # key not found
            v = await func(*args, **kwargs)
            with contextlib.suppress(ValueError):  # value too large
                func.cache[k] = v
            return v

        return wrapper

    return decorator


def safe_create_task(
    coro: Coroutine,
    err_msg: str | None = None,
    name: str | None = None,
    logger: logging.Logger = logging,  # type: ignore
):
    def _task_inner(t: asyncio.Task):
        if t.cancelled():
            logger.warning(f"Task {task.get_name()} was cancelled")
        elif exc := t.exception():
            logger.error(
                err_msg or f"Unexpected error during task {task.get_name()}",
                exc_info=exc,
            )

    task = asyncio.create_task(coro, name=name)
    task.add_done_callback(_task_inner)
    return task


RE_PLAYER_STEAM_64_ID = re.compile(r"^\d{17}$")
RE_PLAYER_UUID = re.compile(r"^[0-9a-f]{32}$")


def get_player_id_type(player_id: str) -> PlayerIDType:
    if RE_PLAYER_STEAM_64_ID.match(player_id):
        return PlayerIDType.STEAM_64_ID
    elif RE_PLAYER_UUID.match(player_id):
        return PlayerIDType.UUID
    else:
        raise ValueError("Unknown player ID type")


def validate_url(url: str, *, strict: bool = False) -> str:
    if not strict and not url.startswith(("http://", "https://")):
        url = "https://" + url

    split_url = urllib.parse.urlsplit(url.strip())
    if not split_url.scheme:
        raise ValueError("URL must start with a scheme (`http://` or `https://`)")
    if split_url.scheme not in ("http", "https"):
        raise ValueError("URL must start with either `http://` or `https://`")
    if not split_url.netloc:
        raise ValueError("Not a valid URL")

    return urllib.parse.urlunsplit(split_url)


class SingletonMeta(type):
    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super().__call__(*args, **kwargs)
        return cls._instances[cls]


class Singleton(metaclass=SingletonMeta):
    pass


T = TypeVar("T")


def batched(iterable: Sequence[T], n=1) -> Iterable[Iterable[T]]:
    length = len(iterable)
    for ndx in range(0, length, n):
        yield iterable[ndx : min(ndx + n, length)]


def game_switch(game: Game, hll_value: T, hllv_value: T) -> T:
    match game:
        case Game.HLL:
            return hll_value
        case Game.HLLV:
            return hllv_value
        case _:
            assert_never(game)
            raise ValueError(f"Unrecognized game: {game}")
