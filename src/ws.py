import json
from asyncio import sleep

import websockets
from websockets.exceptions import ConnectionClosedError, InvalidStatusCode

# TODO add better protection for connection failed
import clone
from common.schemas import NewTestRun
from settings import settings


async def connect_websocket():
    while True:
        try:
            domain = settings.CYKUBE_API_URL[settings.CYKUBE_API_URL.find('//') + 2:]
            async with websockets.connect(f'wss://{domain}/agent/ws',
                  extra_headers={'Authorization': f'Bearer {settings.API_TOKEN}'}) as ws:
                while True:
                    data = json.loads(await ws.recv())
                    cmd = data['command']
                    if cmd == 'start':
                        await clone.start_run(NewTestRun.parse_raw(data['payload']))

        except ConnectionClosedError:
            await sleep(1)
        except TimeoutError:
            await sleep(1)
        except ConnectionRefusedError:
            await sleep(60)
        except InvalidStatusCode:
            await sleep(10)


if __name__ == '__main__':
    connect_websocket()
