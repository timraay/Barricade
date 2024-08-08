import asyncio
from cachetools import TTLCache
from cachetools.keys import hashkey
from functools import wraps
import logging
import re

from barricade.enums import PlayerIDType

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
            try:
                func.cache[k] = v
            except ValueError:
                pass  # value too large
            return v
        return wrapper
    return decorator

def log_task_error(task: asyncio.Task, message: str = None):
    def _task_inner_db(t: asyncio.Task):
        if t.exception():
            logging.error(
                message or "Unexpected error during task",
                exc_info=t.exception()
            )
    task.add_done_callback(_task_inner_db)
    return task

RE_PLAYER_STEAM_64_ID = re.compile(r"^\d{17}$")
RE_PLAYER_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")

def get_player_id_type(player_id: str) -> PlayerIDType:
    if RE_PLAYER_STEAM_64_ID.match(player_id):
        return PlayerIDType.STEAM_64_ID
    elif RE_PLAYER_UUID.match(player_id):
        return PlayerIDType.UUID
    else:
        raise ValueError("Unknown player ID type")

class SingletonMeta(type):
    _instances = {}
    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super(SingletonMeta, cls).__call__(*args, **kwargs)
        return cls._instances[cls]
    
class Singleton(metaclass=SingletonMeta):
    pass
