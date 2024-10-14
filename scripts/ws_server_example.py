import asyncio
import itertools
import logging
from typing import Annotated, AsyncIterator
from fastapi import Depends, FastAPI, Header, WebSocket, WebSocketDisconnect, WebSocketException, status
import pydantic
import uvicorn

from barricade.integrations.custom.models import ServerRequestType, RequestBody, ResponseBody

app = FastAPI()

class BarricadeRequestError(Exception):
    pass

class BarricadeConnectionManager:
    """Simple class to group together active WS connections"""
    def __init__(self):
        self.active_connections: list['BarricadeWebSocket'] = []
        self._counter = itertools.count()

    async def connect(self, ws: 'BarricadeWebSocket'):
        await ws._ws.accept()
        self.active_connections.append(ws)

        try:
            async for content in ws:
                try:
                    request = content["request"]
                except KeyError:
                    logging.error("Received malformed Barricade request: %s", content)
                    continue

                try:
                    if request:
                        await ws.handle_request(RequestBody.model_validate(content))
                    else:
                        await ws.handle_response(ResponseBody.model_validate(content))
                except pydantic.ValidationError:
                    logging.error("Received malformed Barricade request: %s", content)
                    continue
                
        finally:
            self.disconnect(ws)

    def disconnect(self, ws: 'BarricadeWebSocket'):
        self.active_connections.remove(ws)

    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

    async def broadcast(self, request_type: ServerRequestType, payload: dict | None):
        # Very minimalistic way of broadcasting; Check the documentation of
        # whichever library you are using and use the appropriate methods.
        for ws in self.active_connections:
            try:
                return await ws.send_request(request_type, payload)
            except (asyncio.TimeoutError, BarricadeRequestError):
                # These are already logged by send_request
                pass

class BarricadeWebSocket:
    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws
        self._counter = itertools.count()
        self._waiters: dict[int, asyncio.Future] = {}

    async def __aiter__(self) -> AsyncIterator[dict]:
        try:
            while True:
                yield await self._ws.receive_json()
        except WebSocketDisconnect:
            pass

    async def send_request(self, request_type: ServerRequestType, payload: dict | None) -> dict | None:
        # Send request
        request = RequestBody(
            id=next(self._counter),
            request=request_type,
            payload=payload,
        )
        await self._ws.send_json(request.model_dump())

        # Allocate response waiter
        fut = asyncio.Future()
        self._waiters[request.id] = fut

        try:
            # Wait for response
            response: ResponseBody = await asyncio.wait_for(fut, timeout=10)
        except asyncio.TimeoutError:
            logging.error("Barricade did not respond in time to request: %r", request)
            raise
        except BarricadeRequestError as e:
            logging.error("Barricade returned error \"%s\" for request: %r", e, request)
            raise
        finally:
            # Remove waiter
            if request.id in self._waiters:
                del self._waiters[request.id]
        
        return response.response
        
    async def handle_request(self, request: RequestBody):
        # Right now we are just quickly mirroring the payload, kind of like an echo server.
        # In reality you want to handle all various ClientRequestTypes.
        response = request.response_ok(request.payload)
        await self._ws.send_json(response.model_dump())

    async def handle_response(self, response: ResponseBody):
        waiter = self._waiters.get(response.id)

        # Make sure response is being awaited
        if not waiter:
            logging.warning("Discarding response since it is not being awaited: %r", response)
            return
        
        # Make sure waiter is still available
        if waiter.done():
            logging.warning("Discarding response since waiter is already marked done: %r", response)
            return
        
        # Set response
        if response.failed:
            response_data = response.response or {}
            waiter.set_exception(
                BarricadeRequestError(response_data.get("error", ""))
            )
        else:
            waiter.set_result(response.response)


manager = BarricadeConnectionManager()

async def authorize(
        authorization: Annotated[str, Header()] = "",
):
    # Demonstration of how to add authenthication. Of course this is not secure at all.
    # Check if the request includes an "Authorization" header with bearer token.
    print('token:', authorization)
    bearer = authorization.lower()
    if bearer.startswith("bearer: ") or bearer.startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        if token == "password":
            return token

    # Note that this returns a 403 if we raise this before accepting the connection.
    raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid token provided")

@app.websocket("/ws")
async def ws_endpoint(
        ws: WebSocket,
        token: Annotated[str, Depends(authorize)],
):
    await manager.connect(BarricadeWebSocket(ws))

if __name__ == '__main__':
    uvicorn.run(app, host="localhost", port=8000)
