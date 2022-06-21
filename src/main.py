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
import schemas
import testruns
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


@app.get('/testrun/{sha}/status')
def get_status(sha: str):
    """
    Private API - called within the cluster by cypress-runner to get the testrun status
    """
    # return 204 if we're still building - the runners can wait

    tr = testruns.get_run(sha)
    if not tr:
        raise HTTPException(404)

    return {'status': tr.status}


@app.post('/testrun/start')
def start_testrun(testrun: schemas.NewTestRun):
    clone.start_run(testrun)


@app.post('/testrun/{sha}/status/{status}')
def get_status(sha: str, status: schemas.Status):
    tr = testruns.get_run(sha)
    if tr:
        tr.status = status
    notify_status(tr)
    return {"message": "OK"}


@app.put('/testrun/{sha}/specs')
def update_testrun(sha: str, files: List[str]):
    tr = testruns.set_specs(sha, files)
    notify_status(tr)
    return {"message": "OK"}


@app.get('/testrun/{sha}/next')
def get_next_spec(sha: str):
    """
    Private API - called within the cluster by cypress-runner to get the next file to test
    """
    tr = testruns.get_run(sha)
    if len(tr.files) > 0 and tr.status == schemas.Status.running:
        return {"spec": tr.remaining.pop()}

    notify_status(tr)

    raise HTTPException(204)


@app.post('/upload/cache')
def upload(file: UploadFile):
    path = os.path.join(settings.CACHE_DIR, file.filename)
    if os.path.exists(path):
        return {"message": "Exists"}
    try:
        with open(path, "wb") as dest:
            shutil.copyfileobj(file.file, dest)
    finally:
        file.file.close()
    return {"message": "OK"}


@app.post('/testrun/{sha}/{file}/completed')
async def runner_completed(sha: str, file: str, request: Request):
    tr = testruns.get_run(sha)
    if not tr or tr.status != schemas.Status.running:
        raise HTTPException(204)
    # the body will be a tar of the results
    tf = tarfile.TarFile(fileobj=BytesIO(await request.body()))
    tf.extractall(os.path.join(settings.RESULTS_DIR, sha))

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
