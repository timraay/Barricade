import asyncio
from datetime import datetime
from enum import Enum
import inspect
import json
import logging
import random
from typing import AsyncIterator, Awaitable, Callable
import pydantic
import websockets
import itertools
from urllib.parse import urlparse, urlunparse

import websockets.legacy
import websockets.legacy.client

from barricade.exceptions import IntegrationCommandError


class ClientRequestType(str, Enum):
    BAN_PLAYERS = "ban_players"
    UNBAN_PLAYERS = "unban_players"
    NEW_REPORT = "new_report"

class ServerRequestType(str, Enum):
    SCAN_PLAYERS = "scan_players"

class RequestBody(pydantic.BaseModel):
    id: int
    request: ClientRequestType | ServerRequestType
    payload: dict | None = None

    def response_ok(self, payload: dict | None = None):
        return ResponseBody(id=self.id, response=payload)

    def response_error(self, error: str):
        return ResponseBody(id=self.id, response={'error': error}, failed=True)

class ResponseBody(pydantic.BaseModel):
    id: int
    request: None = None
    response: dict | None
    failed: bool = False

class UnbanPlayersRequestConfigPayload(pydantic.BaseModel):
    banlist_id: str | None
class BanPlayersRequestConfigPayload(UnbanPlayersRequestConfigPayload):
    reason: str

class BanPlayersRequestPayload(pydantic.BaseModel):
    player_ids: dict[str, str | None]
    config: BanPlayersRequestConfigPayload

class ScanPlayersRequestPayload(pydantic.BaseModel):
    player_ids: list[str]

class UnbanPlayersRequestPayload(pydantic.BaseModel):
    # Even though in theory these can all be converted to ints, we should safely
    # filter out all invalid record IDs later.
    record_ids: list[str] = pydantic.Field(alias="ban_ids")
    config: UnbanPlayersRequestConfigPayload

class NewReportRequestPayloadPlayer(pydantic.BaseModel):
    player_id: str
    player_name: str
    bm_rcon_url: str | None
class NewReportRequestPayload(pydantic.BaseModel):
    created_at: datetime
    body: str
    reasons: list[str]
    attachment_urls: list[str]
    players: list[NewReportRequestPayloadPlayer]

BACKOFF_MIN = 1.92
BACKOFF_MAX = 60.0
BACKOFF_FACTOR = 1.618
BACKOFF_INITIAL = 5

async def reconnect(
        ws_factory: websockets.legacy.client.Connect,
        logger: logging.Logger,
) -> AsyncIterator[websockets.WebSocketClientProtocol]:
    # Modified version of Connect.__aiter__ which reconnects
    # with exponential backoff, unless a 401 or 403 is returned
    backoff_delay = BACKOFF_MIN
    while True:
        try:
            async with ws_factory as protocol:
                yield protocol
        except Exception as e:
            # If we fail to authorize ourselves we raise instead of backoff
            if isinstance(e, websockets.InvalidStatusCode):
                if e.status_code in (403, 1008):
                    raise
            
            # Add a random initial delay between 0 and 5 seconds.
            # See 7.2.3. Recovering from Abnormal Closure in RFC 6544.
            if backoff_delay == BACKOFF_MIN:
                initial_delay = random.random() * BACKOFF_INITIAL
                logger.info(
                    "WS connection failed; reconnecting in %.1f seconds",
                    initial_delay,
                    exc_info=True,
                )
                await asyncio.sleep(initial_delay)
            else:
                logger.info(
                    "WS connection failed again; retrying in %d seconds",
                    int(backoff_delay),
                    exc_info=True,
                )
                await asyncio.sleep(int(backoff_delay))
            # Increase delay with truncated exponential backoff.
            backoff_delay = backoff_delay * BACKOFF_FACTOR
            backoff_delay = min(backoff_delay, BACKOFF_MAX)
            continue
        else:
            # Connection succeeded - reset backoff delay
            backoff_delay = BACKOFF_MIN

class WebsocketRequestException(Exception):
    pass

class WebsocketRequestHandler:
    def __init__(self, ws: 'Websocket'):
        self.ws = ws

    async def scan_players(self, payload: dict | None) -> dict | None:
        raise NotImplementedError

class Websocket:
    def __init__(
        self,
        address: str,
        token: str | None = None,
        request_handler_factory: Callable[['Websocket'], WebsocketRequestHandler] = WebsocketRequestHandler,
        logger: logging.Logger = logging # type: ignore
    ):
        self.address = address
        self.token = token
        self.logger = logger

        self._ws_task: asyncio.Task | None = None
        # This future can have one of four states:
        # - Pending: The websocket is trying to connect
        # - Cancelled: The websocket is/was disabled
        # - Exception: The connection was rejected
        # - Done: The websocket is connected
        self._ws: asyncio.Future[websockets.WebSocketClientProtocol] = asyncio.Future()
        self._ws.cancel()

        self._waiters: dict[int, asyncio.Future[dict]] = {}
        self._counter = itertools.count()
        
        self.request_handler = request_handler_factory(self)

    def get_url(self):
        # Parse URL
        parsed_url = urlparse(self.address)

        # Overwrite scheme to be "ws"
        if parsed_url.scheme == "https":
            parsed_url = parsed_url._replace(scheme="wss")
        else:
            parsed_url = parsed_url._replace(scheme="ws")

        # Rebuild URL
        return urlunparse(list(parsed_url))

    def is_started(self):
        return self._ws_task is not None
    
    def is_connected(self):
        return self._ws.done() and not self._ws.cancelled()
    
    async def wait_until_connected(self, timeout: float | None = None):
        try:
            return await asyncio.wait_for(asyncio.shield(self._ws), timeout=timeout)
        except asyncio.CancelledError:
            raise RuntimeError("Websocket is stopped")

    def start(self):
        if self.is_started():
            self.stop()
                
        self._ws_task = asyncio.create_task(self._ws_loop())
        self._ws = asyncio.Future()
    
    def stop(self):
        if self._ws_task:
            self._ws_task.cancel()
            self._ws_task = None
        self._ws.cancel()
    
    def update_connection(self):
        self.start()

    async def _ws_loop(self):
        try:
            # Initialize the factory
            ws_factory = websockets.connect(
                self.get_url(),
                extra_headers={
                    'Authorization': f'Bearer {self.token}'
                }
            )

            try:
                # Automatically reconnect with exponential backoff
                async for ws in reconnect(ws_factory, self.logger):
                    # Once connected change the future to done
                    self._ws.set_result(ws)

                    try:
                        # Start listening for messages
                        async for message in ws:
                            try:
                                await self.handle_message(message)
                            except:
                                self.logger.exception("Failed to handle incoming message: %s", message)
                    except websockets.ConnectionClosed:
                        # If the websocket was closed, try reconnecting
                        continue
                    finally:
                        # Change the ws to pending again while we reconnect
                        self._ws = asyncio.Future()
            except websockets.WebSocketException as e:
                self._ws.set_exception(e)

        finally:
            # When exiting the loop, stop the task and cancel the future
            self._ws_task = None
            if not self._ws.done():
                self._ws.cancel()

    async def handle_message(self, message: str | bytes):
        content = json.loads(message)
        try:
            request = content["request"]
        except KeyError:
            self.logger.error("Received malformed websocket request: %s", content)
            return

        try:
            if request:
                await self.handle_request(RequestBody.model_validate(content))
            else:
                await self.handle_response(ResponseBody.model_validate(content))
        except pydantic.ValidationError:
            self.logger.error("Received malformed Barricade request: %s", content)
            return

    async def handle_request(self, request: RequestBody):
        self.logger.info(
            "Handling websocket request #%s %s %s",
            request.id, request.request.name, request.payload
        )
        handler: Callable[[dict | None], Awaitable[dict | None]] | None
        handler = getattr(self.request_handler, request.request.value, None)
        if not inspect.iscoroutinefunction(handler):
            self.logger.warn("No handler for websocket request %s", request.request.name)
            response = request.response_error("No such command")
        else:
            try:
                ret = await handler(request.payload)
                response = request.response_ok(ret)
            except NotImplementedError:
                self.logger.warning('Missing implementation for command %s', request.request)
                response = request.response_error("No such command")
            except WebsocketRequestException as e:
                response = request.response_error(str(e))
            except Exception as e:
                self.logger.exception("Unexpected error while handling %r", request)
                response = request.response_error(str(e))

        # Respond to request
        ws = await self.wait_until_connected(timeout=10)
        await ws.send(response.model_dump_json())

    async def handle_response(self, response: ResponseBody):
        self.logger.info("Handling websocket response #%s %s", response.id, response.response)
        waiter = self._waiters.get(response.id)

        # Make sure response is being awaited
        if not waiter:
            self.logger.warning("Discarding response since it is not being awaited: %r", response)
            return
        
        # Make sure waiter is still available
        if waiter.done():
            self.logger.warning("Discarding response since waiter is already marked done: %r", response)
            return
        
        # Set response
        response_body: dict = response.response # type: ignore
        if response.failed:
            waiter.set_exception(
                IntegrationCommandError(
                    response_body,
                    response_body.get("error", ""),
                )
            )
        else:
            waiter.set_result(response_body)

    async def execute(self, request_type: ClientRequestType, payload: dict | None) -> dict | None:
        # First make sure websocket is connected
        ws = await self.wait_until_connected(2)

        # Send request
        request = RequestBody(
            id=next(self._counter),
            request=request_type,
            payload=payload
        )
        request_dump = request.model_dump_json()
        await ws.send(request_dump)
        
        # Allocate response waiter
        fut = asyncio.Future()
        self._waiters[request.id] = fut

        self.logger.info(
            "Sent websocket request #%s %s %s",
            request.id, request.request.name, request.payload
        )

        try:
            try:
                # Wait for and return response
                return await asyncio.wait_for(fut, timeout=10)
            except asyncio.TimeoutError:
                self.logger.warning((
                    "Websocket did not respond in time to request, retransmitting and"
                    " waiting another 5 seconds: %r"
                ), request)

                ws = await self.wait_until_connected(2)
                await ws.send(request_dump)

                try:
                    return await asyncio.wait_for(fut, timeout=5)
                except asyncio.TimeoutError:
                    self.logger.error("Websocket did not respond in time to request: %r", request)
                    raise
        except IntegrationCommandError as e:
            self.logger.error("Websocket returned error \"%s\" for request: %r", e, request)
            raise
        finally:
            # Remove waiter
            if request.id in self._waiters:
                del self._waiters[request.id]
