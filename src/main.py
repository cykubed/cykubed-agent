import asyncio
import os
import shutil
from typing import Any, AnyStr, Dict, List

from fastapi import FastAPI, HTTPException, UploadFile
from loguru import logger
from starlette.middleware.cors import CORSMiddleware
from uvicorn.config import (
    Config,
)
from uvicorn.server import Server, ServerState  # noqa: F401  # Used to be defined here.

import jobs
import testruns
from settings import settings
from ws import connect_websocket

app = FastAPI()

origins = [
    'http://localhost:4201',
    'https://cypresshub.kisanhub.com',
    'https://cypresskube.ddns.net'
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    # allow_origin_regex=r"https://.*\.ngrok\.io",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# connect to websocket
connect_websocket()

JSONObject = Dict[AnyStr, Any]

logger.info("** Started server **")

jobs.connect_k8()


@app.get('/hc')
def health_check():
    return {'message': 'OK!'}


@app.get('/testrun/{id}/status')
def get_status(id: int):
    """
    Private API - called within the cluster by cypress-runner to get the testrun status
    """
    # return 204 if we're still building - the runners can wait

    tr = testruns.get_run(id)
    if not tr:
        raise HTTPException(404)

    return {'status': tr.status}


@app.put('/testrun/{id}/specs')
def update_testrun(id: int, files: List[str]):
    testruns.set_specs(id, files)


@app.get('/testrun/{id}/next')
def get_next_spec(id: int):
    """
    Private API - called within the cluster by cypress-runner to get the next file to test
    """
    spec = testruns.get_next_spec(id)
    if spec:
        logger.info(f"Returning spec {spec.file}")
        return {"spec": spec.file}
    raise HTTPException(204)


@app.post('/upload/{type}/')
def upload(file: UploadFile):
    path = os.path.join(settings.NPM_CACHE_DIR, type, file.filename)
    if not os.path.exists(path):
        shutil.copy(file.filename, path)


# @app.post('/testrun/{specid}/completed')
# async def runner_completed(specid: int, request: Request, db: Session = Depends(get_db)):
#     """
#     Private API - called within the cluster by cypress-runner when a test spec has completed
#     """
#     logger.info(f"mark spec completed {specid}")
#     spec = crud.mark_completed(db, id)
#     # the body will be a tar of the results
#     tf = tarfile.TarFile(fileobj=BytesIO(await request.body()))
#     tf.extractall(os.path.join(settings.RESULTS_DIR, spec.testrun.sha))
#
#     testrun = spec.testrun
#     remaining = crud.get_remaining(db, testrun)
#     # has the entire run finished?
#     if not remaining:
#         logging.info(f"Creating report for {testrun.sha}")
#
#         notify(testrun)
#
#     return "OK"


def create_file_path(sha: str, path: str) -> str:
    i = path.find('build/results/')
    return f'{settings.RESULT_URL}/{sha}/{path[i+14:]}'


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
