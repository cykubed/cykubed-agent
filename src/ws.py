import asyncio
import json
import signal
from asyncio import sleep, exceptions

import websockets
from kubernetes_asyncio.client import ApiException
from loguru import logger
from websockets.exceptions import ConnectionClosedError, InvalidStatusCode, ConnectionClosed

import jobs
import logs
from app import app
from common import schemas
from common.exceptions import InvalidTemplateException, BuildFailedException
from common.schemas import NewTestRun, TestRunBuildState
from jobs import handle_delete_build_states
from k8utils import async_delete_snapshot
from settings import settings


async def handle_start_run(tr: NewTestRun):
    """
    Start a test run
    :param app:
    :param tr: new test run
    """
    logger.info(f'Handle start testrun {tr.id}')
    try:
        # kick off a new build job
        await jobs.handle_new_run(tr)
    except (InvalidTemplateException, ApiException):
        logger.exception(f"Failed to start test run {tr.id}", tr=tr)
        await app.httpclient.post(f'/agent/testrun/{tr.id}/status/failed')


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
        elif cmd == 'delete_testruns':
            bsmodels = [TestRunBuildState.parse_obj(x) for x in json.loads(payload)]
            await handle_delete_build_states(bsmodels)
        elif cmd == 'cancel':
            await jobs.handle_run_completed(NewTestRun.parse_raw(payload))
        elif cmd == 'delete_snapshots':
            for name in payload['names']:
                await async_delete_snapshot(name)
        elif cmd == 'build_completed':
            await jobs.handle_build_completed(NewTestRun.parse_raw(payload))
        elif cmd == 'cache_prepared':
            await jobs.handle_cache_prepared(NewTestRun.parse_raw(payload))
        elif cmd == 'run_completed':
            await jobs.handle_run_completed(NewTestRun.parse_raw(payload))
        else:
            logger.error(f'Unexpected command {cmd} - ignoring')
    except BuildFailedException as ex:
        logger.error(f'Build failed\n{ex.msg}', trid=ex.testrun_id)
        if ex.testrun_id:
            await app.httpclient.post(f'/agent/testrun/{ex.testrun_id}/error',
                    content=schemas.TestRunErrorReport(stage=ex.stage,
                                                       msg=ex.msg).json())

    except Exception as ex:
        logger.exception(f'Failed to handle msg: {ex}')


# async def log_upload_loop(websocket):
#     while app.is_running():
#         try:
#             msgitem = await redis.blpop('messages', 10)
#             if msgitem:
#                 rawmsg = msgitem[1]
#                 asyncio.create_task(handle_agent_message(websocket, rawmsg))
#         except RedisError as ex:
#             if not app.is_running():
#                 return
#             logger.error(f"Failed to fetch messages from Redis: {ex}")
#         except Exception as ex:
#             logger.exception(f'Unexpected exceptoin in producer_handler: {ex}')
#
#


async def consumer_handler(websocket):
    while app.is_running():
        try:
            message = await websocket.recv()
        except ConnectionClosed:
            return
        await handle_websocket_message(json.loads(message))


async def producer_handler(websocket):
    while app.is_running():
        try:
            msgitem = await logs.msgqueue.get()
            if msgitem:
                await websocket.send(msgitem)
        except ConnectionClosedError:
            return
        except Exception as ex:
            logger.error(type(ex))
            logger.exception(f'Unexpected exception in producer_handler: {ex}')
            raise ex


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
        logger.warning(f"SIGTERM/SIGINT caught: close socket and exit")
        wsock = app.ws
        if wsock:
            app.shutdown()
            await wsock.close()
            await wsock.wait_closed()
        signal.raise_signal(signal.SIGINT)

    loop = asyncio.get_event_loop()
    loop.add_signal_handler(signal.SIGTERM, lambda: asyncio.create_task(handle_sigterm_runner()))
    app.wait_period = 2

    while app.is_running():
        try:
            logger.debug("Try to connect websocket")
            domain = settings.MAIN_API_URL[settings.MAIN_API_URL.find('//') + 2:]
            protocol = 'wss' if settings.MAIN_API_URL.startswith('https') else 'ws'
            url = f'{protocol}://{domain}/agent-ws'

            async with websockets.connect(url, extra_headers=headers) as ws:
                logger.info("Connected")
                app.wait_period = 2
                app.ws_connected = True

                done, pending = await asyncio.wait([asyncio.create_task(consumer_handler(ws)),
                                                       asyncio.create_task(producer_handler(ws))],
                                                   return_when=asyncio.FIRST_COMPLETED)
                # we'll need to reconnect
                for task in pending:
                    task.cancel()

            app.ws_connected = False
            logger.info(f"Socket disconnected: try again in {app.wait_period}s")
            await asyncio.sleep(app.wait_period)
            app.wait_period = min(60, 2 * app.wait_period)

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
            logger.debug('Connection refused: try later...')
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




