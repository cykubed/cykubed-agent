import asyncio
import json
import signal
from asyncio import sleep, exceptions, create_task

import websockets
from loguru import logger
from redis.exceptions import RedisError
from websockets.exceptions import ConnectionClosedError, InvalidStatusCode, ConnectionClosed

import cache
import db
import jobs
import state
from app import app
from common.enums import AgentEventType
from common.redisutils import async_redis, ping_redis, get_specfile_log_key
from common.schemas import NewTestRun, AgentEvent, AgentTestRunErrorEvent, \
    AgentBuildCompletedEvent
from settings import settings


async def handle_start_run(tr: NewTestRun):
    """
    Start a test run
    :param app:
    :param tr: new test run
    """
    logger.info(f'Handle start testrun {tr.id}')
    try:
        # Store in Redis and kick off a new build job
        await db.new_testrun(tr)
        # and create a new one
        if settings.K8:
            await jobs.handle_new_run(tr)
        else:
            # local testing
            st = state.TestRunBuildState(trid=tr.id, rw_build_pvc='dummy')
            await st.save()
            logger.info(f'Now run the runner with args "clone {tr.id}"', tr=tr)
    except:
        logger.exception(f"Failed to start test run {tr.id}", tr=tr)
        await app.httpclient.post(f'/agent/testrun/{tr.id}/status/failed')


async def handle_delete_project(project_id: int):
    logger.info(f'Deleting project {project_id}')
    if settings.K8:
        await jobs.delete_jobs_for_project(project_id)


async def handle_fetch_log(trid: int, file: str):
    logger.info(f'Fetch cypress log for testrun {trid} and spec {file}')
    key = get_specfile_log_key(trid, file)
    logs = await async_redis().lrange(key, 0, -1)
    if logs:
        log = ''.join(logs)
        await app.httpclient.post(f'/agent/testrun/{trid}/spec-log', {'file': file, 'log': log})


async def handle_websocket_message(data: dict):
    """
    Handle a message from the websocket
    :param data:
    :return:
    """
    try:
        cmd = data['command']
        payload = data['payload']
        logger.debug(f'Received {cmd} command')
        if cmd == 'start':
            await handle_start_run(NewTestRun.parse_raw(payload))
        elif cmd == 'delete_project':
            await handle_delete_project(payload['project_id'])
        elif cmd == 'cancel':
            st = await state.get_build_state(payload['testrun_id'])
            if st:
                await jobs.delete_pvcs(st)
                await st.notify_run_completed()
        elif cmd == 'clear_cache':
            await cache.clear_cache()
        elif cmd == 'fetch_log':
            await handle_fetch_log(data['testrun_id'], data['spec'])
    except Exception as ex:
        logger.exception(f'Failed to handle msg: {ex}')


async def consumer_handler(websocket):
    while app.is_running():
        try:
            message = await websocket.recv()
        except ConnectionClosed:
            return
        asyncio.create_task(handle_websocket_message(json.loads(message)))


async def handle_agent_message(websocket, rawmsg: str):
    event = AgentEvent.parse_raw(rawmsg)
    # logger.debug(f'Msg: {event.type} for {event.testrun_id}')
    if event.type == AgentEventType.build_completed:
        # build completed - create runner jobs
        await jobs.handle_build_completed(AgentBuildCompletedEvent.parse_raw(rawmsg))
    elif event.type == AgentEventType.cache_prepared:
        await jobs.handle_cache_prepared(event.testrun_id)
    elif event.type == AgentEventType.error:
        await jobs.handle_testrun_error(AgentTestRunErrorEvent.parse_raw(rawmsg))
    elif event.type == AgentEventType.run_completed:
        # run completed - notify and clean up
        await jobs.handle_run_completed(event.testrun_id)
    else:
        # post everything else through the websocket
        await websocket.send(rawmsg)


async def producer_handler(websocket):
    redis = async_redis()
    while app.is_running():
        try:
            msgitem = await redis.blpop('messages', 10)
            if msgitem:
                rawmsg = msgitem[1]
                asyncio.create_task(handle_agent_message(websocket, rawmsg))
        except RedisError as ex:
            if not app.is_running():
                return
            logger.error(f"Failed to fetch messages from Redis: {ex}")
        except Exception as ex:
            logger.exception(f'Unexpected exceptoin in producer_handler: {ex}')


async def connect():
    """
    Connect to the main cykube servers via a websocket
    """
    # fetch token
    headers = {'Authorization': f'Bearer {settings.API_TOKEN}',
               'Agent-Version': settings.AGENT_VERSION,
               'Agent-Host': app.hostname}
    if app.region:
        headers['Agent-Region'] = app.region

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
                app.ws_connected = True
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
            app.ws_connected = False
            await sleep(1)
        except ConnectionRefusedError:
            app.ws_connected = False
            await sleep(10)
        except OSError:
            logger.warning("Cannot ensure_connection to cykube - sleep for 60 secs")
            app.ws_connected = False
            await sleep(10)
        except InvalidStatusCode as ex:
            if ex.status_code == 403:
                logger.error("Permission denied: please check that you have used the correct API token")
            app.ws_connected = False
            await sleep(10)
        except Exception as ex:
            logger.exception('Could not connect: try later...')
            app.ws_connected = False
            await sleep(10)




