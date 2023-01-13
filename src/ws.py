import asyncio
import json
import sys
from asyncio import sleep, exceptions

import websockets
from loguru import logger
from websockets.exceptions import ConnectionClosedError, InvalidStatusCode

# TODO add better protection for connection failed
import jobs
import status
from common import schemas
from common.schemas import NewTestRun
from logs import configure_logging
from settings import settings

mainsocket: websockets.WebSocketClientProtocol = None


def start_run(newrun: NewTestRun):
    if settings.K8:
        # stop existing jobs
        jobs.delete_jobs_for_branch(newrun.id, newrun.branch)
        # and create a new one
        jobs.create_build_job(newrun)
    else:
        logger.info(f"Now run cykuberunner with options 'build {newrun.project.id} {newrun.local_id}'")


async def send_log(source: str, project_id: int, local_id: int, msg):
    global mainsocket
    if mainsocket:
        await mainsocket.send(schemas.AgentLogMessage(ts=msg.record['time'],
                                                  project_id=project_id,
                                                  local_id=local_id,
                                                  level=str(msg.record['level']),
                                                  msg=msg,
                                                  source=source).json())


async def send_status_update(project_id: int, local_id: int, status: schemas.TestRunStatus):
    global mainsocket
    if mainsocket:
        await mainsocket.send(schemas.AgentStatusMessage(
                                                      project_id=project_id,
                                                      local_id=local_id,
                                                      status=status).json())


async def connect_websocket():
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
                    data = json.loads(await ws.recv())
                    cmd = data['command']
                    logger.info(f"Received command {cmd}")
                    payload = data['payload']
                    if cmd == 'start':
                        try:
                            tr = NewTestRun.parse_raw(payload)
                            start_run(tr)
                        except:
                            logger.exception(f"Failed to start test run {tr.id}", trid=tr.id)
                            await send_status_update(tr.project.id, tr.local_id, 'failed')
                    elif cmd == 'cancel':
                        project_id = payload['project_id']
                        local_id = payload['local_id']
                        if settings.K8:
                            jobs.delete_jobs(project_id, local_id)

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
        await asyncio.sleep(1)
        logger.info(f'Test {i}', project_id=11, local_id=12)
        await asyncio.sleep(100)
        i += 1


async def run():
    await asyncio.gather(connect_websocket(),
                         test_logging())


if __name__ == '__main__':
    try:
        configure_logging()
        asyncio.run(run())
    except KeyboardInterrupt:
        sys.exit(0)
