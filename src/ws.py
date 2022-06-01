import json
from asyncio import sleep

import websockets
from websockets.exceptions import ConnectionClosedError

# TODO add better protection for connection failed
import clone
from settings import settings


async def connect_websocket():
    while True:
        try:
            domain = settings.CYKUBE_MAIN_URL[settings.CYKUBE_MAIN_URL.find('//')+2:]
            async with websockets.connect(f'ws://{domain}/ws',
                  extra_headers={'Authorization': f'Bearer {settings.API_TOKEN}'}) as ws:
                while True:
                    data = json.loads(await ws.recv())
                    cmd = data['command']
                    if cmd == 'start':
                        await clone.start_run(data['payload'])

        except ConnectionClosedError:
            await sleep(1)
        except ConnectionRefusedError:
            await sleep(60)
