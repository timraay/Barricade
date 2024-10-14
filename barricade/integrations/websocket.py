import asyncio
import logging
import random
from typing import AsyncIterator
import websockets
from urllib.parse import urlparse, urlunparse

import websockets.legacy
import websockets.legacy.client

from barricade.utils import safe_create_task

BACKOFF_MIN = 1.92
BACKOFF_MAX = 300.0
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

class Websocket:
    def __init__(
        self,
        address: str,
        token: str | None = None,
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

    def get_url(self) -> str:
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
        return self._ws.done() and not self._ws.cancelled() and not self._ws.exception()
    
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

                    asyncio.create_task(self._invoke_setup_hook(ws))

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
        
    async def _invoke_setup_hook(self, ws: websockets.WebSocketClientProtocol):
        try:
            await self.setup_hook()
        except:
            self.logger.exception("Failed to invoke setup hook")
            await ws.close()

    async def handle_message(self, message: str | bytes):
        pass

    async def setup_hook(self):
        pass
