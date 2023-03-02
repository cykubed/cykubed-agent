import asyncio
import os
import shutil
from datetime import datetime

import aiofiles.os
import sentry_sdk
from fastapi import FastAPI, UploadFile
from fastapi_exceptions.exceptions import NotFound
from fastapi_utils.tasks import repeat_every
from loguru import logger
from sentry_sdk.integrations.asyncio import AsyncioIntegration
from sentry_sdk.integrations.httpx import HttpxIntegration
from starlette.responses import Response, PlainTextResponse
from uvicorn.config import (
    Config,
)
from uvicorn.server import Server, ServerState  # noqa: F401  # Used to be defined here.

import messages
import mongo
import ws
from appstate import shutdown
from common import k8common
from common.enums import TestRunStatus, AgentEventType
from common.schemas import CompletedBuild, AgentLogMessage, AgentCompletedBuildMessage, AgentSpecCompleted, \
    AgentStatusChanged, NewTestRun, CompletedSpecFile, AgentSpecStarted
from common.settings import settings
from common.utils import disable_hc_logging, utcnow
from jobs import create_runner_jobs
from logs import configure_logging

if os.environ.get('SENTRY_DSN'):
    sentry_sdk.init(integrations=[
        HttpxIntegration(),
        AsyncioIntegration(),
    ],)


app = FastAPI()

disable_hc_logging()

logger.info("** Started server **")


@app.get("/sentry-debug")
async def trigger_error():
    division_by_zero = 1 / 0


@app.get('/hc')
def health_check():
    return {'message': 'OK!'}


@app.on_event("shutdown")
async def shutdown_event():
    shutdown()
    if ws.mainsocket:
        await ws.mainsocket.close()


@app.post('/upload')
def upload_cache(file: UploadFile):
    logger.info(f"Uploading file {file.filename} to cache")
    path = os.path.join(settings.CYKUBE_CACHE_DIR, file.filename)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as dest:
            shutil.copyfileobj(file.file, dest)
    finally:
        file.file.close()
    return {"message": "OK"}


@app.post('/log')
def post_log(msg: AgentLogMessage):
    """
    Proxy all log messages up to the main server
    :param msg:
    :return:
    """
    messages.queue.add_agent_msg(msg)


@app.get('/testrun/{pk}', response_model=NewTestRun)
async def get_test_run(pk: int) -> NewTestRun:
    tr = await mongo.get_testrun(pk)
    if not tr:
        raise NotFound()
    return NewTestRun.parse_obj(tr)


@app.get('/testrun/{pk}/next', response_class=PlainTextResponse)
async def get_next_spec(pk: int, response: Response, name: str = None) -> str:
    tr = await mongo.get_testrun(pk)
    if not tr:
        raise NotFound()

    if tr['status'] != 'running':
        response.status_code = 204
        return
    spec = await mongo.assign_next_spec(pk, name)
    if not spec:
        response.status_code = 204
        return

    messages.queue.add_agent_msg(AgentSpecStarted(testrun_id=pk,
                                                  type=AgentEventType.spec_started,
                                                  started=utcnow(),
                                                  pod_name=name,
                                                  file=spec))
    return spec


@app.post('/testrun/{pk}/status/{status}')
async def status_changed(pk: int, status: TestRunStatus):
    await mongo.set_status(pk, status)
    messages.queue.add_agent_msg(AgentStatusChanged(testrun_id=pk,
                                                    type=AgentEventType.status,
                                                    status=status))


@app.post('/testrun/{pk}/spec-completed')
async def spec_completed(pk: int, item: CompletedSpecFile):
    # for now we assume file uploads go straight to cykubemain
    await mongo.spec_completed(pk, item.file)
    messages.queue.add_agent_msg(AgentSpecCompleted(type=AgentEventType.spec_completed, testrun_id=pk, spec=item))


@app.post('/testrun/{pk}/build-complete')
async def build_complete(pk: int, build: CompletedBuild):
    tr = await mongo.set_build_details(pk, build)
    messages.queue.add_agent_msg(AgentCompletedBuildMessage(testrun_id=pk,
                                                            type=AgentEventType.build_completed,
                                                            build=build))

    if settings.K8:
        if not tr.project.start_runners_first:
            create_runner_jobs(tr, build)
    else:
        logger.info(f'Start runner with "./main.py run {pk}', id=pk)

    return {"message": "OK"}


@app.on_event("startup")
@repeat_every(seconds=300)
async def cleanup_cache():
    # remove inactive testruns
    trids = await mongo.get_inactive_testrun_ids()
    if trids:
        # delete the distro
        for name in await aiofiles.os.listdir(settings.CYKUBE_CACHE_DIR):
            trid = int(name.split('.')[0])
            if trid in trids:
                await aiofiles.os.remove(os.path.join(settings.CYKUBE_CACHE_DIR, name))
        # and remove them from the local mongo: it's only need to retain state while a testrun is active
        await mongo.remove_testruns(trids)
    # finally just remove any files (i.e dist caches) that haven't been read in a while
    today = utcnow()
    for name in await aiofiles.os.listdir(settings.CYKUBE_CACHE_DIR):
        path = os.path.join(settings.CYKUBE_CACHE_DIR, name)
        st = await aiofiles.os.stat(path)
        last_read_estimate = datetime.fromtimestamp(st.st_atime)
        if (today - last_read_estimate).days > settings.DIST_CACHE_STATENESS_WINDOW_DAYS:
            await aiofiles.os.remove(path)


# @app.on_event("startup")
# @repeat_every(seconds=120)
# async def cleanup_testruns():
#     # check for specs that are marked as running but have no pod
#     if settings.K8:
#         loop = asyncio.get_running_loop()
#         for doc in await mongo.get_active_specfile_docs():
#             podname = doc['pod_name']
#             is_running = await loop.run_in_executor(None, is_pod_running, podname)
#             if not is_running:
#                 tr = await mongo.get_testrun(doc['trid'])
#                 if tr and tr['status'] == 'running':
#                     file = doc['file']
#                     # let another Job take this - this handles crashes and Spot Jobs
#                     logger.info(f'Cannot find a running pod for {podname}: returning {file} to the pool')
#                     await mongo.reset_specfile(doc)

    # # now check for builds that have gone on for too long
    # for tr in await mongo.get_testruns_with_status(TestRunStatus.building):
    #     duration = (datetime.datetime.utcnow() - tr['started']).seconds
    #     if duration > tr['project']['build_deadline']:
    #         await mongo.delete_testrun(tr['id'])
    #
    # # ditto for runners
    # try:
    #     for tr in await mongo.get_testruns_with_status(TestRunStatus.running):
    #         duration = (datetime.datetime.utcnow() - tr['started']).seconds
    #         if duration > tr['project']['runner_deadline']:
    #             await mongo.delete_testrun(tr['id'])
    # except:
    #     logger.exception("Failed to cleanup testruns")


async def init():
    """
    Run the websocket and server concurrently
    """
    await mongo.init()
    config = Config(app, port=5000, host='0.0.0.0')
    config.setup_event_loop()
    server = Server(config=config)
    await asyncio.gather(ws.connect(), server.serve())


if __name__ == "__main__":
    try:
        if settings.K8 and not settings.TEST:
            k8common.init()
        configure_logging()
        asyncio.run(init())
    except Exception as ex:
        print(ex)

