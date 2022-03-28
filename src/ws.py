import json
from asyncio import sleep
from pprint import pprint

import websockets
from websockets.exceptions import ConnectionClosedError

from settings import settings


# TODO add better protection for connection failed


async def connect_websocket():
    while True:
        try:
            domain = settings.CYKUBE_MAIN_URL[settings.CYKUBE_MAIN_URL.find('//')+2:]
            print(domain)
            async with websockets.connect(f'ws://{domain}/ws',
                  extra_headers={'Authorization': f'Token {settings.API_TOKEN}'}) as ws:
                while True:
                    data = json.loads(await ws.recv())
                    cmd = data['command']
                    if cmd == 'start':
                        print("Start build")
                        pprint(data['payload'], indent=4)
        except ConnectionClosedError:
            await sleep(1)


# asyncio.get_event_loop().run_until_complete(connect_websocket())
