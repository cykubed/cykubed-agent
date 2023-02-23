import asyncio
import os
import shutil

from fastapi import FastAPI, UploadFile
from loguru import logger
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
from common.schemas import CompletedBuild, AgentLogMessage, AgentCompletedBuildMessage, AgentSpecCompleted, SpecResult, \
    AgentStatusChanged, NewTestRun
from common.settings import settings
from common.utils import disable_hc_logging
from jobs import create_runner_jobs
from logs import configure_logging

app = FastAPI()

disable_hc_logging()

logger.info("** Started server **")


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
    os.makedirs(settings.CYKUBE_CACHE_DIR, exist_ok=True)
    path = os.path.join(settings.CYKUBE_CACHE_DIR, file.filename)
    if os.path.exists(path):
        return {"message": "Exists"}
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
    return NewTestRun.parse_obj(tr)


@app.get('/testrun/{pk}/next', response_class=PlainTextResponse)
async def get_next_spec(pk: int, response: Response, name: str = None) -> str:
    tr = await mongo.get_testrun(pk)
    if tr['status'] != 'running':
        response.status_code = 204
        return
    spec = await mongo.assign_next_spec(pk, name)
    if not spec:
        response.status_code = 204
        return
    return spec


@app.post('/testrun/{pk}/status/{status}')
async def status_changed(pk: int, status: TestRunStatus):
    messages.queue.add_agent_msg(AgentStatusChanged(testrun_id=pk,
                                                    type=AgentEventType.status,
                                                    status=status))


@app.post('/testrun/{pk}/spec-completed')
async def spec_completed(pk: int, result: SpecResult):
    # for now we assume file uploads go straight to cykubemain
    await mongo.spec_completed(pk, result.file)
    messages.queue.add_agent_msg(AgentSpecCompleted(testrun_id=pk,
                                                    type=AgentEventType.spec_completed,
                                                    result=result))


@app.post('/testrun/{pk}/build-complete')
async def build_complete(pk: int, build: CompletedBuild):
    tr = await mongo.set_build_details(pk, build)
    messages.queue.add_agent_msg(AgentCompletedBuildMessage(testrun_id=pk,
                                                            type=AgentEventType.build_completed,
                                                            build=build))

    if settings.K8:
        create_runner_jobs(tr)
    else:
        logger.info(f'Start runner with "./main.py run {pk}', id=pk)

    return {"message": "OK"}


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

