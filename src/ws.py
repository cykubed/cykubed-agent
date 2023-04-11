import asyncio
import json
from asyncio import sleep, exceptions, create_task

import websockets
from httpx import HTTPError
from loguru import logger
from redis import ResponseError
from websockets.exceptions import ConnectionClosedError, InvalidStatusCode, ConnectionClosed

import common.redisutils
import db
import jobs
from app import is_running
from common.enums import AgentEventType
from common.redisutils import async_redis
from common.schemas import NewTestRun, AgentEvent, AgentCompletedBuildMessage
from common.settings import settings
from messages import queue


async def handle_start_run(app, tr: NewTestRun):
    """
    Start a test run
    :param app:
    :param tr: new test run
    """
    try:
        # Store in Redis and kick off a new build job
        await db.new_testrun(tr)
        # and create a new one
        await jobs.create_build_job(tr)
    except:
        logger.exception(f"Failed to start test run {tr.id}", tr=tr)
        await app['httpclient'].post(f'/agent/testrun/{tr.id}/status/failed')


async def handle_delete_project(project_id: int):
    if settings.K8:
        jobs.delete_jobs_for_project(project_id)


async def handle_cancel_run(trid: int):
    if settings.K8:
        jobs.delete_jobs(trid)
    await db.cancel_testrun(trid)


async def handle_message(app, data: dict):
    """
    Handle a message from the websocket
    :param app:
    :param data:
    :return:
    """
    cmd = data['command']
    payload = data['payload']
    if cmd == 'start':
        await handle_start_run(app, NewTestRun.parse_raw(payload))
    elif cmd == 'delete_project':
        await handle_delete_project(payload['project_id'])
    elif cmd == 'cancel':
        await handle_cancel_run(payload['testrun_id'])


async def consumer_handler(app, websocket):
    while is_running():
        try:
            message = await websocket.recv()
            await handle_message(app, json.loads(message))
        except ConnectionClosed:
            return


async def producer_handler(websocket):
    while is_running():
        msg = await queue.get() + '\n'
        await websocket.send(msg)
        queue.task_done()


class Retry(object):
    def __init__(self):
        self.delay = 10

    async def retry(self):
        logger.warning(f"Failed to contact Cykube servers: please check your token")
        await asyncio.sleep(self.delay)
        self.delay = min(settings.MAX_HTTP_BACKOFF, self.delay + 10)

    def reset(self):
        self.delay = 10


async def connect(app):
    """
    Connect to the main cykube servers via a websocket
    """

    # fetch token
    headers = {'Authorization': f'Bearer {settings.API_TOKEN}'}

    retrier = Retry()
    while is_running():

        # grab a token
        try:
            resp = await app['httpclient'].post('/agent/wsconnect', json={'host_name': app['hostname']})
            if resp.status_code != 200:
                await retrier.retry()
                continue

            token = resp.text
        except HTTPError:
            await retrier.retry()
            continue

        if token:
            try:
                logger.debug("Try to connect websocket")
                domain = settings.MAIN_API_URL[settings.MAIN_API_URL.find('//') + 2:]
                protocol = 'wss' if settings.MAIN_API_URL.startswith('https') else 'ws'
                url = f'{protocol}://{domain}/agent/ws?token={token}'
                async with websockets.connect(url, extra_headers=headers) as ws:
                    logger.info("Connected")
                    done, pending = await asyncio.wait([create_task(consumer_handler(app, ws)),
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


async def poll_messages(app, max_messages=None):
    """
    Poll the message queue, forwarding them all to the websocket
    :param app: app for state
    :param max_messages: limit the number of messages sent (for unit testing)
    """
    sent = 0
    logger.info("Start polling messages from Redis")
    redis = async_redis()

    while is_running():
        try:
            msglist = await redis.lpop('messages', 100)
            if msglist is not None:
                for rawmsg in msglist:
                    event = AgentEvent.parse_raw(rawmsg)
                    if event.type == AgentEventType.build_completed:
                        # build completed - create runner jobs
                        buildmsg = AgentCompletedBuildMessage.parse_raw(rawmsg)
                        tr = await common.redisutils.get_testrun(buildmsg.testrun_id)
                        await jobs.create_runner_jobs(tr, buildmsg)
                        # and notify the server
                        resp = await app['httpclient'].post(f'/agent/testrun/{tr.id}/build-completed',
                                                           content=rawmsg.encode())
                        if resp.status_code != 200:
                            logger.error(f'Failed to update server that build was completed:'
                                         f' {resp.status_code}: {resp.text}')
                    else:
                        # otherwise it must be a log message
                        queue.add(rawmsg)
                    sent += 1
                    if max_messages and sent == max_messages:
                        # for easier testing
                        return
        except ResponseError:
            if not is_running():
                return
            logger.exception("Failed to fetch messages from Redis")
            if max_messages:
                return
        except Exception as ex:
            logger.exception("Unexpected exception during message poll")
            if max_messages:
                return

        await asyncio.sleep(1)
