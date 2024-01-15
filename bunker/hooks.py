import asyncio
from enum import Enum
from typing import Callable, Coroutine, Any

from bunker import schemas
from bunker.utils import log_task_error

class EventHooks(Enum):
    report_create: list[Callable[[schemas.Report, schemas.ReportCreateParams], Coroutine]] = list()
    integration_add: list[Callable[[Any], Coroutine]] = list()
    integration_update: list[Callable[[Any], Coroutine]] = list()
    integration_remove: list[Callable[[Any], Coroutine]] = list()

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
    def invoke_integration_add(integration):
        EventHooks._invoke(EventHooks.integration_add, integration)

    @staticmethod
    def invoke_integration_update(integration):
        EventHooks._invoke(EventHooks.integration_update, integration)

    @staticmethod
    def invoke_integration_remove(integration):
        EventHooks._invoke(EventHooks.integration_remove, integration)

def add_hook(hook_type: EventHooks):
    def _add_hook_inner(func: Callable[[Any], Coroutine]):
        hook_type.value.append(func)
        return func
    return _add_hook_inner
