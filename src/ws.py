import asyncio
import json
from asyncio import sleep, exceptions

import websockets
from loguru import logger
from websockets.exceptions import ConnectionClosedError, InvalidStatusCode, ConnectionClosed

import appstate
import asyncmongo
import jobs
from common.schemas import NewTestRun
from common.settings import settings
from messages import queue

mainsocket = None


async def start_run(newrun: NewTestRun):
    # Store in Mongo and kick off a new build job
    await asyncmongo.new_run(newrun)
    # and create a new one
    await jobs.create_build_job(newrun)


async def delete_project(project_id: int):
    if settings.K8:
        jobs.delete_jobs_for_project(project_id)


async def handle_message(data):
    """
    Handle a message from the websocket
    :param data:
    :return:
    """
    cmd = data['command']
    payload = data['payload']
    if cmd == 'start':
        tr = NewTestRun.parse_raw(payload)
        try:
            await start_run(tr)
        except:
            logger.exception(f"Failed to start test run {tr.id}", tr=tr)
            if queue and tr:
                await queue.send_status_update(tr.id, 'failed')
    elif cmd == 'delete_project':
        project_id = payload['project_id']
        await delete_project(project_id)
    elif cmd == 'cancel':
        testrun_id = payload['testrun_id']
        if settings.K8:
            jobs.delete_jobs(testrun_id)
            await asyncmongo.cancel_testrun(testrun_id)


async def consumer_handler(websocket):
    while appstate.is_running():
        try:
            message = await websocket.recv()
            await handle_message(json.loads(message))
        except ConnectionClosed:
            return


async def producer_handler(websocket):
    while appstate.is_running():
        msg = await queue.get() + '\n'
        await websocket.send(msg)
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
    while appstate.is_running():
        logger.info("Starting websocket")
        try:
            domain = settings.MAIN_API_URL[settings.MAIN_API_URL.find('//') + 2:]
            protocol = 'wss' if settings.MAIN_API_URL.startswith('https') else 'ws'
            url = f'{protocol}://{domain}/agent/ws'
            async with websockets.connect(url,
                                          extra_headers={
                                              'AgentName': settings.AGENT_NAME,
                                              'Authorization': f'Bearer {settings.API_TOKEN}'}) as ws:
                global mainsocket
                mainsocket = ws
                logger.info("Connected")
                done, pending = await asyncio.wait([consumer_handler(ws), producer_handler(ws)],
                                                   return_when=asyncio.FIRST_COMPLETED)
                # we'll need to reconnect
                for task in pending:
                    task.cancel()
        except ConnectionClosedError:
            if not appstate.is_running():
                return
            await sleep(1)
        except exceptions.TimeoutError:
            await sleep(1)
        except ConnectionRefusedError:
            await sleep(10)
        except OSError:
            logger.warning("Cannot ensure_connection to cykube - sleep for 60 secs")
            await sleep(10)
        except InvalidStatusCode:
            await sleep(10)

