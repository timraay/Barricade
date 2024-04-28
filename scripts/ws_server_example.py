import itertools
from typing import Annotated
from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect, WebSocketException, status
import uvicorn

from bunker.integrations.websocket import RequestBody

app = FastAPI()

async def authenticate(
        token: Annotated[str | None, Query()] = None,
):
    # Demonstration of how to add authenthication. Of course this is not secure at all.
    # Check if a "token" query parameter matches "password".
    print('token:', token)
    if (token != "password"):
        raise WebSocketException(code=status.HTTP_401_UNAUTHORIZED, reason="Unauthorized")

    return token

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []
        self._counter = itertools.count()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active_connections.append(ws)

        try:
            while True:
                # Receive requests
                data = await ws.receive_json()
                # Answer requests
                body = RequestBody.model_validate(data)
                await ws.send_json(body.response(body.payload).model_dump())
        except WebSocketDisconnect:
            pass
        finally:
            self.disconnect(ws)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

    async def broadcast(self, message: str):
        for connection in self.active_connections:
            await connection.send_text(message)

manager = ConnectionManager()

@app.websocket("/ws")
async def ws_endpoint(
        ws: WebSocket,
        token: Annotated[str, Depends(authenticate)],
):
    await manager.connect(ws)

if __name__ == '__main__':
    uvicorn.run(app, host="localhost", port=8000)
