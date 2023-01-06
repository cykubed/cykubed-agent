import asyncio
import json
import socket
import sys
from asyncio import sleep, exceptions

import httpx
import websockets
from loguru import logger
from websockets.exceptions import ConnectionClosedError, InvalidStatusCode

# TODO add better protection for connection failed
import jobs
from common.logupload import upload_exception_trace, upload_log_line
from common.schemas import NewTestRun
from settings import settings

shutting_down = False
mainsocket = None


def start_run(newrun: NewTestRun):
    if settings.JOB_MODE == 'k8':
        # stop existing jobs
        jobs.delete_jobs_for_branch(newrun.id, newrun.branch)
        # and create a new one
        jobs.create_build_job(newrun)
    else:
        logger.info(f"Now run cykuberunner with options 'build {newrun.id}'")


async def connect_websocket():
    while not shutting_down:
        try:
            domain = settings.MAIN_API_URL[settings.MAIN_API_URL.find('//') + 2:]
            protocol = 'wss' if settings.MAIN_API_URL.startswith('https') else 'ws'
            url = f'{protocol}://{domain}/agent/ws'
            logger.info(f"Starting websocket {url}")
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
                            start_run(tr)
                        except:
                            upload_log_line(tr.id, "Failed to start run")
                            logger.exception(f"Failed to start test run {tr.id}")
                            upload_exception_trace(tr.id)
                            r = httpx.put(f'{settings.AGENT_URL}/testrun/{tr.id}/status/failed')
                            if r.status_code != 200:
                                logger.error("Failed to mark run as cancelled")
                    elif cmd == 'cancel':
                        testrun_id = payload['testrun_id']
                        if settings.JOB_MODE == 'k8':
                            # TODO delete the K8 jobs
                            pass

        except ConnectionClosedError:
            if shutting_down:
                return
            await sleep(1)
        except exceptions.TimeoutError:
            await sleep(1)
        except ConnectionRefusedError:
            await sleep(60)
        except OSError:
            logger.warning("Cannot connect to cykube - sleep for 60 secs")
            await sleep(10)
        except InvalidStatusCode:
            await sleep(10)


if __name__ == '__main__':
    try:
        asyncio.run(connect_websocket())
    except KeyboardInterrupt:
        sys.exit(0)
