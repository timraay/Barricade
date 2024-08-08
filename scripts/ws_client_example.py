
import asyncio
from barricade.integrations.websocket import Websocket, ClientRequestType


async def main():
    ws = Websocket("ws://localhost:8000/ws", token="password")
    ws.start()
    await ws.wait_until_connected(timeout=3)

    print(await asyncio.gather(
        ws.execute(ClientRequestType.NEW_REPORT, {"a": 1}),
        ws.execute(ClientRequestType.NEW_REPORT, {"a": 2}),
        ws.execute(ClientRequestType.NEW_REPORT, {"a": 3}),
    ))

if __name__ == '__main__':
    asyncio.run(main())
