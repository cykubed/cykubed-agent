import json
from asyncio import sleep

import websockets
from websockets.exceptions import ConnectionClosedError

import crud
from crud import sessionmaker
from settings import settings
# TODO add better protection for connection failed
from tasks import clone_and_build


async def start_build(testrun):
    with sessionmaker.context_session() as db:
        tr = crud.create_testrun(db, testrun)
        clone_and_build.delay(tr.id)


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
                        await start_build(data['payload'])

        except ConnectionClosedError:
            await sleep(1)


# asyncio.get_event_loop().run_until_complete(connect_websocket())
