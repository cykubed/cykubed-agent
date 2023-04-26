import asyncio
import json
import signal
from asyncio import sleep, exceptions, create_task

import websockets
from loguru import logger
from redis.exceptions import RedisError
from websockets.exceptions import ConnectionClosedError, InvalidStatusCode, ConnectionClosed

import db
import jobs
from app import app
from common.enums import AgentEventType
from common.redisutils import async_redis, ping_redis
from common.schemas import NewTestRun, AgentEvent
from settings import settings


async def handle_start_run(tr: NewTestRun):
    """
    Start a test run
    :param app:
    :param tr: new test run
    """
    try:
        # Store in Redis and kick off a new build job
        await db.new_testrun(tr)
        # and create a new one
        await jobs.create_clone_job(tr)
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


async def handle_message(data: dict):
    """
    Handle a message from the websocket
    :param data:
    :return:
    """
    cmd = data['command']
    payload = data['payload']
    if cmd == 'start':
        await handle_start_run(NewTestRun.parse_raw(payload))
    elif cmd == 'delete_project':
        await handle_delete_project(payload['project_id'])
    elif cmd == 'cancel':
        await handle_cancel_run(payload['testrun_id'])


async def consumer_handler(websocket):
    while app.is_running():
        try:
            message = await websocket.recv()
            await handle_message(json.loads(message))
        except ConnectionClosed:
            return


async def producer_handler(websocket):
    redis = async_redis()
    while app.is_running():
        try:
            msglist = await redis.lpop('messages', 100)
            if msglist is not None:
                for rawmsg in msglist:
                    event = AgentEvent.parse_raw(rawmsg)
                    if event.type == AgentEventType.clone_completed:
                        # clone completed - kick off the build
                        await jobs.create_build_job(event.testrun_id)
                    if event.type == AgentEventType.build_completed:
                        # build completed - create runner jobs
                        await jobs.build_completed(event.testrun_id)
                        # and notify the server
                        resp = await app.httpclient.post(f'/agent/testrun/{event.testrun_id}/build-completed',
                                                            content=rawmsg.encode())
                        if resp.status_code != 200:
                            logger.error(f'Failed to update server that build was completed:'
                                         f' {resp.status_code}: {resp.text}')
                    # now post throught the websocket
                    await websocket.send(rawmsg)

            await asyncio.sleep(1)
        except RedisError as ex:
            if not app.is_running():
                return
            logger.error(f"Failed to fetch messages from Redis: {ex}")


async def connect():
    """
    Connect to the main cykube servers via a websocket
    """
    # fetch token
    headers = {'Authorization': f'Bearer {settings.API_TOKEN}',
               'Agent-Host': app.hostname}

    async def handle_sigterm_runner():
        logger.warning(f"SIGTERM/SIGINT caught: close socket and exist")
        wsock = app.ws
        if wsock:
            app.shutdown()
            await wsock.close()
            await wsock.wait_closed()
        signal.raise_signal(signal.SIGINT)

    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGTERM, lambda: asyncio.create_task(handle_sigterm_runner()))

    while app.is_running():

        try:
            logger.debug("Try to connect websocket")
            domain = settings.MAIN_API_URL[settings.MAIN_API_URL.find('//') + 2:]
            protocol = 'wss' if settings.MAIN_API_URL.startswith('https') else 'ws'
            url = f'{protocol}://{domain}/agent-ws'

            async with websockets.connect(url, extra_headers=headers) as ws:
                logger.info("Connected")
                done, pending = await asyncio.wait([create_task(consumer_handler(ws)),
                                                    create_task(producer_handler(ws))],
                                                   return_when=asyncio.FIRST_COMPLETED)
                # we'll need to reconnect
                for task in pending:
                    task.cancel()

            if not ping_redis():
                logger.error("Cannot contact redis: wait until we can")
            else:
                logger.info("Socket disconnected: try again shortly")
            await asyncio.sleep(10)

        except KeyboardInterrupt:
            await app.shutdown()
            return
        except ConnectionClosedError as ex:
            if not app.is_running():
                logger.info('Agent terminating gracefully')
                return
            if ex.code == 1001:
                logger.warning("Server closed - reconnect a bit later")
                await sleep(10)
            else:
                logger.debug('Connection closed - attempt to reconnect')
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




