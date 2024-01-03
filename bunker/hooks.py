import asyncio
from enum import Enum
from typing import Callable, Coroutine, Any

from bunker import schemas
from bunker.utils import log_task_error

class EventHooks(Enum):
    report_create: list[Callable[[schemas.Report, schemas.ReportCreateParams], Coroutine]] = list()
    service_add: list[Callable[[Any], Coroutine]] = list()
    service_update: list[Callable[[Any], Coroutine]] = list()
    service_remove: list[Callable[[Any], Coroutine]] = list()

    @staticmethod
    def _invoke(hook_type: 'EventHooks', *args):
        return [
            log_task_error(
                task=asyncio.create_task(hook(*args)),
                message=f"Failed to invoke {hook_type.name} hook {hook.__name__}"
            ) for hook in hook_type.value
        ]

    @staticmethod
    def invoke_report_create(report: schemas.Report, params: schemas.ReportCreateParams):
        EventHooks._invoke(EventHooks.report_create, report, params)

    @staticmethod
    def invoke_service_add(service):
        EventHooks._invoke(EventHooks.service_add, service)

    @staticmethod
    def invoke_service_update(service):
        EventHooks._invoke(EventHooks.service_update, service)

    @staticmethod
    def invoke_service_remove(service):
        EventHooks._invoke(EventHooks.service_remove, service)

def add_hook(hook_type: EventHooks):
    def _add_hook_inner(func: Callable[[Any], Coroutine]):
        hook_type.value.append(func)
        return func
    return _add_hook_inner
