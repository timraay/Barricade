import asyncio
from collections import defaultdict
from enum import Enum
from typing import Callable, Coroutine, Any

from bunker import schemas
from bunker.utils import log_task_error

class EventHooks(str, Enum):
    report_create = "report_create"
    report_delete = "report_delete"
    player_ban = "player_ban"
    player_unban = "player_unban"

    __hooks__: dict['EventHooks', list[Callable[[Any], Coroutine]]] = defaultdict(list)

    def _invoke(self, *args):
        return [
            log_task_error(
                task=asyncio.create_task(hook(*args)),
                message=f"Failed to invoke {self.name} hook {hook.__name__}"
            ) for hook in self.get()
        ]

    def get(self):
        return EventHooks.__hooks__[self]
    
    def register(self, func: Callable[[Any], Coroutine]):
        self.get().append(func)
        return func

    @staticmethod
    def invoke_report_create(report: schemas.ReportWithToken):
        return EventHooks.report_create._invoke(report)

    @staticmethod
    def invoke_report_delete(report: schemas.ReportWithRelations):
        return EventHooks.report_delete._invoke(report)

    @staticmethod
    def invoke_player_ban(response: schemas.Response):
        return EventHooks.player_ban._invoke(response)

    @staticmethod
    def invoke_player_unban(response: schemas.Response):
        return EventHooks.player_unban._invoke(response)

def add_hook(hook_type: EventHooks):
    return hook_type.register
