from asyncio import sleep

import websockets
from websockets.exceptions import ConnectionClosedError

from settings import settings


async def connect_websocket():
    while True:
        try:
            async with websockets.connect(f'ws://localhost:5002/ws',
                  extra_headers={'Authorization': f'Token {settings.API_TOKEN}'}) as ws:
                while True:
                    data = await ws.recv()
                    print(data)
        except ConnectionClosedError:
            await sleep(1)


# asyncio.get_event_loop().run_until_complete(connect_websocket())
