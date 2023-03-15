import asyncio
import json
from asyncio import sleep, exceptions, create_task

import httpx
import websockets
from httpx import HTTPError
from loguru import logger
from websockets.exceptions import ConnectionClosedError, InvalidStatusCode, ConnectionClosed

import jobs
import messages
from common import db
from common.db import async_redis
from common.enums import AgentEventType
from common.schemas import NewTestRun, AgentEvent, AgentCompletedBuildMessage
from common.settings import settings
from messages import queue

_running = True


def is_running() -> bool:
    global _running
    return _running


def shutdown():
    global _running
    _running = False


async def start_run(tr: NewTestRun):
    try:
        # Store in Redis and kick off a new build job
        await db.new_testrun(tr)
        # and create a new one
        await jobs.create_build_job(tr)
    except:
        logger.exception(f"Failed to start test run {tr.id}", tr=tr)
        if queue and tr:
            await queue.send_status_update(tr.id, 'failed')


async def delete_project(project_id: int):
    if settings.K8:
        jobs.delete_jobs_for_project(project_id)


async def cancel_run(trid: int):
    if settings.K8:
        jobs.delete_jobs(trid)
    await db.cancel_testrun(trid)


async def handle_message(data: dict):
    """
    Handle a message from the websocket
    :param data:
    :return:
    """
    cmd = data['command']
    payload = data['payload']
    if cmd == 'start':
        await start_run(NewTestRun.parse_raw(payload))
    elif cmd == 'delete_project':
        await delete_project(payload['project_id'])
    elif cmd == 'cancel':
        await cancel_run(payload['testrun_id'])


async def consumer_handler(websocket):
    while is_running():
        try:
            message = await websocket.recv()
            await handle_message(json.loads(message))
        except ConnectionClosed:
            return


async def producer_handler(websocket):
    while is_running():
        msg = await queue.get() + '\n'
        await websocket.send(msg)
        queue.task_done()


async def connect():
    """
    Connect to the main cykube servers via a websocket
    """

    # fetch token
    headers = {'Authorization': f'Bearer {settings.API_TOKEN}'}

    # transport = httpx.AsyncHTTPTransport(retries=settings.MAX_HTTP_RETRIES)
    async with httpx.AsyncClient(headers=headers) as client:

        while is_running():

            # grab a token
            try:
                resp = await client.post(f'{settings.MAIN_API_URL}/agent/wsconnect',
                                         json={'name': settings.AGENT_NAME})
                token = resp.text
            except HTTPError as ex:
                logger.warning(f"Failed to contact Cykube servers ({ex}): please check your token")
                await asyncio.sleep(10)
                continue

            try:
                logger.info("Try to connect websocket")
                domain = settings.MAIN_API_URL[settings.MAIN_API_URL.find('//') + 2:]
                protocol = 'wss' if settings.MAIN_API_URL.startswith('https') else 'ws'
                url = f'{protocol}://{domain}/agent/ws?token={token}'
                async with websockets.connect(url, extra_headers=headers) as ws:
                    logger.info("Connected")
                    done, pending = await asyncio.wait([create_task(consumer_handler(ws)),
                                                        create_task(producer_handler(ws))],
                                                       return_when=asyncio.FIRST_COMPLETED)
                    # we'll need to reconnect
                    for task in pending:
                        task.cancel()
            except ConnectionClosedError:
                if not is_running():
                    return
                await sleep(1)
            except exceptions.TimeoutError:
                logger.debug('Could not connect: try later...')
                await sleep(1)
            except ConnectionRefusedError:
                await sleep(10)
            except OSError:
                logger.warning("Cannot ensure_connection to cykube - sleep for 60 secs")
                await sleep(10)
            except InvalidStatusCode as ex:
                if ex.status_code == 403:
                    logger.error("Permission denied: please check that you have used the correct API token")
                await sleep(10)
            except Exception as ex:
                logger.exception('Could not connect: try later...')
                await sleep(10)


async def handle_build_completed(msg: AgentCompletedBuildMessage):
    tr = await db.get_testrun(msg.testrun_id)
    jobs.create_runner_jobs(tr, msg)


async def poll_messages(max_messages=None):
    """
    Poll the message queue, forwarding them all to the websocket
    :param max_messages: limit the number of messages sent (for unit testing)
    """
    sent = 0
    logger.info("Start polling messages from Redis")
    async with async_redis().pubsub() as pubsub:
        await pubsub.psubscribe('messages')
        while is_running():
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message is not None:
                msg = message['data']
                event = AgentEvent.parse_raw(msg)
                messages.queue.add(msg)
                if event.type == AgentEventType.build_completed:
                    await handle_build_completed(AgentCompletedBuildMessage.parse_raw(msg))
                sent += 1
                if max_messages and sent == max_messages:
                    # for easier testing
                    return
            else:
                await asyncio.sleep(5)


async def init():
    """
    Run the websocket and server concurrently
    """
    await messages.queue.init()
    async with asyncio.TaskGroup() as tg:
        tg.create_task(connect())
        tg.create_task(poll_messages())


