import asyncio
import json
import logging
import pydantic
import websockets
import itertools
from urllib.parse import urlencode, urlparse, parse_qsl, urlunparse

class RequestBody(pydantic.BaseModel):
    id: int
    request: str
    payload: dict | None = None

    def response(self, payload: dict | None = None):
        return ResponseBody(id=self.id, response=payload)

class ResponseBody(pydantic.BaseModel):
    id: int
    response: dict | None

class Websocket:
    def __init__(self, address: str, token: str = None):
        self.address = address
        self.token = token

        self._ws_task: asyncio.Task = None
        # This future can have one of three states:
        # - Pending: The websocket is trying to connect
        # - Cancelled: The websocket is/was disabled
        # - Done: The websocket is connected
        self._ws: asyncio.Future[websockets.WebSocketClientProtocol] = asyncio.Future().cancel()

        self._waiters: dict[int, asyncio.Future[dict]] = {}
        self._counter = itertools.count()

    def get_url(self):
        # Parse URL
        parsed_url = urlparse(self.address)

        # Overwrite scheme to be "ws"
        parsed_url.scheme = "ws"

        # Add token to query params
        if self.token is not None:
            # Extract query params and add "token"
            query = dict(parse_qsl(parsed_url.query))
            query["token"] = self.token
            # Re-encode query params
            parsed_url.query = urlencode(query)

        # Rebuild URL
        return urlunparse(list(parsed_url))

    def is_started(self):
        return self._ws_task is not None
    
    def is_connected(self):
        return self._ws is not None
    
    async def wait_until_connected(self, timeout: float = None):
        return await asyncio.wait_for(asyncio.shield(self._ws), timeout=timeout)

    def start(self):
        if self.is_started():
            self.stop()
                
        self._ws_task = asyncio.create_task(self._ws_loop())
        self._ws = asyncio.Future()
    
    def stop(self) -> bool:
        if self._ws_task:
            self._ws_task.cancel()
        self._ws.cancel()
    
    def update_connection(self):
        self.start()


    async def _ws_loop(self):
        try:
            # Initialize the factory
            ws_factory = websockets.connect(self.get_url())

            # Automatically reconnect with exponential backoff
            async for ws in ws_factory:
                # Once connected change the future to done
                print("connected!")
                self._ws.set_result(ws)

                try:
                    # Start listening for messages
                    async for message in ws:
                        try:
                            await self.handle_message(message)
                        except:
                            logging.exception("Failed to handle incoming message: %s", message)
                except websockets.ConnectionClosed:
                    # If the websocket was closed, try reconnecting
                    continue
                finally:
                    # Change the ws to pending again while we reconnect
                    self._ws = asyncio.Future()

        finally:
            # When exiting the loop, stop the task and cancel the future
            self._ws_task = None
            self._ws.cancel()


    async def execute(self, request: str, payload: dict = None):
        try:
            # First make sure websocket is connected
            ws = await self.wait_until_connected(2)
        except asyncio.CancelledError:
            # Websocket is stopped
            raise RuntimeError("Websocket is stopped")
        except asyncio.TimeoutError:
            # Took too long to connect
            raise
        else:
            # Send request
            req_id = next(self._counter)
            body = RequestBody(id=req_id, request=request, payload=payload)
            await ws.send(body.model_dump_json())
            
            # Allocate response waiter
            fut = asyncio.Future()
            self._waiters[req_id] = fut

            try:
                # Wait for and return response
                return await asyncio.wait_for(fut, timeout=10)
            finally:
                # Remove waiter
                if req_id in self._waiters:
                    del self._waiters[req_id]

    async def handle_message(self, message: str):
        obj = json.loads(message)

        if 'response' in obj:
            body = ResponseBody.model_validate(obj)
        
            if waiter := self._waiters.get(body.id):
                waiter.set_result(body.response)
                del self._waiters[body.id]
            else:
                logging.warning("Discarding response with ID %s since it is not being awaited", body.id)
        
        elif 'request' in obj:
            body = RequestBody.model_validate(obj)
            print("New request:", body.request, body.payload)

            # Respond to request
            ws = await self.wait_until_connected(timeout=10)
            ws.send(body.response().model_dump())

        else:
            raise ValueError("Unknown message")

        
