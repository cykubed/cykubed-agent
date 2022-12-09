import json
import socket
from asyncio import sleep

import websockets
from loguru import logger
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
                    payload = data['payload']
                    if cmd == 'start':
                        await clone.start_run(NewTestRun.parse_raw(payload))
                    elif cmd == 'cancel':
                        testrun_id = payload['testrun_id']
                        # TODO delete the K8 jobs
                        logger.info(f"Deleting jobs for test run {testrun_id}")

        except ConnectionClosedError:
            await sleep(1)
        except TimeoutError:
            await sleep(1)
        except ConnectionRefusedError:
            await sleep(60)
        except socket.gaierror:
            await sleep(60)
        except InvalidStatusCode:
            await sleep(10)


if __name__ == '__main__':
    connect_websocket()
