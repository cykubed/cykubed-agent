import asyncio
import os
import shutil

from fastapi import FastAPI, UploadFile
from loguru import logger
from uvicorn.config import (
    Config,
)
from uvicorn.server import Server, ServerState  # noqa: F401  # Used to be defined here.

import messages
import status
import ws
from common import k8common
from common.schemas import CompletedBuild, AgentLogMessage
from common.settings import settings
from common.utils import disable_hc_logging
from jobs import create_runner_jobs
from logs import configure_logging
from messages import update_status

app = FastAPI()

disable_hc_logging()

logger.info("** Started server **")


@app.get('/hc')
def health_check():
    return {'message': 'OK!'}


@app.on_event("shutdown")
async def shutdown_event():
    status.shutdown()
    if ws.mainsocket:
        await ws.mainsocket.close()


@app.post('/upload')
def upload_cache(file: UploadFile):
    logger.info(f"Uploading file {file.filename} to cache")
    os.makedirs(settings.CACHE_DIR, exist_ok=True)
    path = os.path.join(settings.CACHE_DIR, file.filename)
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


@app.post('/build-complete')
async def build_complete(build: CompletedBuild):
    if settings.K8:
        create_runner_jobs(build)
    else:
        logger.info(f'Start runner with "./main.py run {build.testrun.project.id} {build.testrun.local_id} '
                    f'{build.cache_hash}"', tr=build.testrun)
    update_status(build.testrun.project.id, build.testrun.local_id, 'running')
    return {"message": "OK"}


async def create_tasks():
    """
    Run the websocket and server concurrently
    """
    config = Config(app, port=5000, host='0.0.0.0')
    config.setup_event_loop()
    server = Server(config=config)
    await asyncio.gather(ws.connect(), server.serve())


if __name__ == "__main__":
    try:
        if settings.K8:
            k8common.init()
        configure_logging()
        asyncio.run(create_tasks())
    except Exception as ex:
        print(ex)

