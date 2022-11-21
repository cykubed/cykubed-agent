import asyncio
import os
import shutil
import tarfile
from io import BytesIO
from typing import Any, AnyStr, Dict, List

from fastapi import FastAPI, HTTPException, UploadFile
from loguru import logger
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from uvicorn.config import (
    Config,
)
from uvicorn.server import Server, ServerState  # noqa: F401  # Used to be defined here.

import clone
import jobs
import testruns
from common import enums, schemas
from cykube import notify_run_completed, notify_status
from settings import settings
from ws import connect_websocket

app = FastAPI()

origins = [
    'http://localhost:4201',
    'http://localhost:5000'
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    # allow_origin_regex=r"https://.*\.ngrok\.io",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

JSONObject = Dict[AnyStr, Any]

logger.info("** Started server **")

jobs.connect_k8()


@app.get('/hc')
def health_check():
    return {'message': 'OK!'}


@app.get('/testrun/{id}', response_model=schemas.TestRun)
def get_testrun(id: int):
    """
    Private API - called within the cluster by cypress-runner to get the testrun status
    """
    tr = testruns.get_run(id)
    if not tr:
        raise HTTPException(404)

    return tr


@app.post('/testrun/start')
def start_testrun(testrun: schemas.NewTestRun):
    clone.start_run(testrun)


@app.post('/testrun/{id}/status/{status}')
async def get_status(id: int, status: enums.Status):
    tr = testruns.get_run(id)
    if tr:
        tr.status = status
    await notify_status(tr)
    return {"message": "OK"}


@app.put('/testrun/{id}/specs')
async def update_testrun(id: int, files: List[str]):
    tr = testruns.set_specs(id, files)
    await notify_status(tr)
    return {"message": "OK"}


@app.get('/testrun/{id}/next')
async def get_next_spec(id: int):
    """
    Private API - called within the cluster by cypress-runner to get the next file to test
    """
    tr = testruns.get_run(id)
    if len(tr.files) > 0 and tr.status == enums.Status.running:
        return {"spec": tr.remaining.pop()}

    await notify_status(tr)

    raise HTTPException(204)


@app.post('/upload/cache')
def upload(file: UploadFile):
    os.makedirs(settings.CACHE_DIR, exist_ok=True)
    path = os.path.join(settings.CACHE_DIR, file.filename)
    if os.path.exists(path):
        return {"message": "Exists"}
    try:
        with open(path, "wb") as dest:
            shutil.copyfileobj(file.file, dest)
    finally:
        file.file.close()
    return {"message": "OK"}


@app.post('/testrun/{id}/{file}/completed')
async def runner_completed(id: int, file: str, request: Request):
    tr = testruns.get_run(id)
    if not tr or tr.status != enums.Status.running:
        raise HTTPException(204)
    # the body will be a tar of the results
    tf = tarfile.TarFile(fileobj=BytesIO(await request.body()))
    tf.extractall(os.path.join(settings.RESULTS_DIR, str(id)))

    # remove file from list
    tr.remaining = [x for x in tr.remaining if x.file != file]
    if not tr.remaining:
        notify_run_completed(tr)
    return "OK"


# @app.on_event("startup")
# @repeat_every(seconds=3000)
# def handle_timeouts():
#     with sessionmaker.context_session() as db:
#         crud.apply_timeouts(db, settings.TEST_RUN_TIMEOUT, settings.SPEC_FILE_TIMEOUT)
#     delete_old_dists()


#
#
# @app.route('/testrun/<key>/create_report', methods=['POST'])
# def report(key):
#     logging.info(f"Create report for {key}")
#     create_report(key)
#

async def create_tasks():
    config = Config(app, port=5000)
    config.setup_event_loop()
    server = Server(config=config)
    t1 = asyncio.create_task(connect_websocket())
    t2 = asyncio.create_task(server.serve())
    await asyncio.gather(t1, t2)


def run2():
    asyncio.run(create_tasks())

    # loop.run_forever()


if __name__ == "__main__":
    run2()
