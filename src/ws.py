import asyncio
import json
import socket
import sys
from asyncio import sleep, exceptions

import websockets
from loguru import logger
from websockets.exceptions import ConnectionClosedError, InvalidStatusCode

# TODO add better protection for connection failed
import jobs
from common.logupload import upload_exception_trace
from common.schemas import NewTestRun
from settings import settings
from testruns import add_run

shutting_down = False
mainsocket = None


async def start_run(newrun: NewTestRun):
    add_run(newrun)

    # stop existing jobs
    jobs.delete_jobs_for_branch(newrun.id, newrun.branch)
    # and create a new one
    jobs.create_build_job(newrun)


async def connect_websocket():
    while not shutting_down:
        logger.info("Starting websocket")
        try:
            domain = settings.MAIN_API_URL[settings.MAIN_API_URL.find('//') + 2:]
            protocol = 'wss' if settings.MAIN_API_URL.startswith('https') else 'ws'
            url = f'{protocol}://{domain}/agent/ws'
            async with websockets.connect(url,
                                          extra_headers={'Authorization': f'Bearer {settings.API_TOKEN}'}) as ws:
                global mainsocket
                mainsocket = ws
                logger.info("Connected")

                while not shutting_down:
                    data = json.loads(await ws.recv())
                    cmd = data['command']
                    logger.info(f"Received command {cmd}")
                    payload = data['payload']
                    if cmd == 'start':
                        try:
                            tr = NewTestRun.parse_raw(payload)
                            await start_run(tr)
                        except:
                            logger.error("Failed to start run")
                            upload_exception_trace(tr.id)
                    elif cmd == 'cancel':
                        testrun_id = payload['testrun_id']
                        # TODO delete the K8 jobs
                        logger.info(f"Deleting jobs for test run {testrun_id}")

        except ConnectionClosedError:
            if shutting_down:
                return
            await sleep(1)
        except exceptions.TimeoutError:
            await sleep(1)
        except ConnectionRefusedError:
            await sleep(60)
        except socket.gaierror:
            await sleep(60)
        except InvalidStatusCode:
            await sleep(10)


if __name__ == '__main__':
    try:
        asyncio.run(connect_websocket())
    except KeyboardInterrupt:
        sys.exit(0)
