import asyncio
import json
import sys
from asyncio import sleep, exceptions

import websockets
from loguru import logger
from websockets.exceptions import ConnectionClosedError, InvalidStatusCode

import jobs
import status
from common.schemas import NewTestRun
from common.settings import settings
from messages import queue

mainsocket = None


def start_run(newrun: NewTestRun):
    if settings.K8:
        # stop existing jobs
        jobs.delete_jobs_for_branch(newrun.id, newrun.branch)
        # and create a new one
        jobs.create_build_job(newrun)
    else:
        logger.info(f"Now run cykuberunner with options 'build {newrun.project.id} {newrun.local_id}'",
                    tr=newrun)


async def consumer_handler(websocket):
    while status.is_running():
        message = await websocket.recv()
        data = json.loads(message)
        cmd = data['command']
        logger.info(f"Received command {cmd}")
        payload = data['payload']
        if cmd == 'start':
            tr = NewTestRun.parse_raw(payload)
            try:
                start_run(tr)
            except:
                logger.exception(f"Failed to start test run {tr.local_id}", tr=tr)
                await queue.send_status_update(tr.project.id, tr.local_id, 'failed')
        elif cmd == 'cancel':
            project_id = payload['project_id']
            local_id = payload['local_id']
            if settings.K8:
                jobs.delete_jobs(project_id, local_id)


async def producer_handler(websocket):
    while status.is_running():
        await websocket.send(await queue.get()+'\n')
        queue.task_done()


async def close():
    global mainsocket
    if mainsocket:
        await mainsocket.close()


async def connect():
    """
    Connect to the main cykube servers via a websocket
    """

    # note that we must initialise the queue here so it refers to the correct event loop
    await queue.init()
    while status.is_running():
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
                while status.is_running():
                    await asyncio.gather(consumer_handler(ws), producer_handler(ws))

        except ConnectionClosedError:
            if not status.is_running():
                return
            await sleep(1)
        except exceptions.TimeoutError:
            await sleep(1)
        except ConnectionRefusedError:
            await sleep(10)
        except OSError:
            logger.warning("Cannot connect to cykube - sleep for 60 secs")
            await sleep(10)
        except InvalidStatusCode:
            await sleep(10)


async def test_logging():
    i = 0
    while status.is_running():
        await asyncio.sleep(5)
        logger.info(f'Test {i}', project_id=11, local_id=12)
        i += 1


async def run():
    await asyncio.gather(connect(),
                         test_logging())


if __name__ == '__main__':
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        sys.exit(0)
