import json
from asyncio import sleep

import websockets
from websockets.exceptions import ConnectionClosedError

# TODO add better protection for connection failed
import clone
from schemas import NewTestRun
from settings import settings


async def connect_websocket():
    while True:
        try:
            domain = settings.CYKUBE_APP_URL[settings.CYKUBE_APP_URL.find('//')+2:]
            async with websockets.connect(f'ws://{domain}/hub/ws',
                  extra_headers={'Authorization': f'Bearer {settings.API_TOKEN}'}) as ws:
                while True:
                    data = json.loads(await ws.recv())
                    cmd = data['command']
                    if cmd == 'start':
                        clone.start_run(NewTestRun.parse_raw(data['payload']))

        except ConnectionClosedError:
            await sleep(1)
        except ConnectionRefusedError:
            await sleep(60)
