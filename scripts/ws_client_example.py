
import asyncio
from bunker.integrations.websocket import Websocket


async def main():
    ws = Websocket("ws://localhost:8000/ws", token="password")
    ws.start()

    await asyncio.gather(
        ws.execute("test", {"a": 1}),
        ws.execute("test", {"a": 2}),
        ws.execute("test", {"a": 3}),
    )

if __name__ == '__main__':
    asyncio.run(main())
