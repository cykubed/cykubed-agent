import asyncio
import logging
import os
import shutil
import tarfile
from io import BytesIO
from typing import List, Any, AnyStr, Dict

from fastapi import Depends, FastAPI, HTTPException, Request
from loguru import logger
from sqlalchemy.orm import Session
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import PlainTextResponse
from uvicorn.config import (
    Config,
)
from uvicorn.server import Server, ServerState  # noqa: F401  # Used to be defined here.

import crud
import jobs
import schemas
from crud import TestRunParams
from cykube import notify
from models import get_db
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


# @app.middleware('http')
async def verify_auth_token(request: Request, call_next):
    if request.url.path != '/hc' and not request.url.path.startswith('/testrun') and \
            request.method != 'OPTIONS':
        if "Authorization" not in request.headers:
            return PlainTextResponse('Missing auth token', status_code=401)
        auth = request.headers["Authorization"].split(' ')
        if len(auth) != 2 or auth[0] != 'Token':
            return PlainTextResponse('Invalid authorization header', status_code=401)
        if auth[1] != settings.API_TOKEN:
            return PlainTextResponse('Invalid token', status_code=401)

    return await call_next(request)


JSONObject = Dict[AnyStr, Any]

logger.info("** Started server **")

jobs.connect_k8()


@app.get('/hc')
def health_check(db: Session = Depends(get_db)):
    crud.count_test_runs(db)
    return {'message': 'OK!'}


@app.get('/testrun/{id}', response_model=schemas.TestRun)
def get_testrun(id: int,
                 db: Session = Depends(get_db)):
    return crud.get_testrun(db, id)


@app.get('/testruns', response_model=List[schemas.TestRun])
def get_testruns(page: int = 1, page_size: int = 50,
                db: Session = Depends(get_db)):
    return crud.get_test_runs(db, page, page_size)


def clear_results(sha: str):
    rdir = os.path.join(settings.RESULTS_DIR, sha)
    os.makedirs(rdir, exist_ok=True)
    shutil.rmtree(rdir, ignore_errors=True)
    os.mkdir(rdir)


@app.post('/api/start', response_model=schemas.TestRun)
def start_testrun(params: TestRunParams, db: Session = Depends(get_db)):
    logger.info(f"Start test run {params.url} {params.branch} {params.sha} {params.parallelism}")
    crud.cancel_previous_test_runs(db, params.sha, params.branch)
    clear_results(params.sha)
    tr = crud.create_testrun(db, TestRunParams(repos=params.url, sha=params.sha, branch=params.branch))
    return tr


@app.post('/api/cancel/{id}')
def cancel_testrun(id: int, db: Session = Depends(get_db)):
    tr = crud.get_testrun(db, id)
    if jobs.batchapi:
        jobs.delete_jobs_for_branch(tr.branch)
    crud.cancel_testrun(db, tr)
    return {'cancelled': 'OK'}


@app.get('/api/testrun/{id}/logs')
def get_testrun_logs(id: int,
                     offset: int = 0) -> str:
    logs = os.path.join(settings.DIST_DIR, f'{id}.log')
    if not os.path.exists(logs):
        raise HTTPException(404)
    with open(os.path.join(settings.DIST_DIR, f'{id}.log')) as f:
        if offset:
            f.seek(offset)
        return f.read()

#
# @app.get('/testrun/{id}/result')
# def get_testrun_result(id: int,
#                        db: Session = Depends(get_db)
#                        ) -> schemas.Results:
#     tr = crud.get_testrun(db, id)
#     json_result = os.path.join(settings.RESULTS_DIR, tr.sha, 'json', 'results.json')
#     if os.path.exists(json_result):
#         result: schemas.Results = schemas.Results.parse_file(json_result)
#         result.testrun = tr
#         return result
#     return schemas.Results(testrun=tr, specs=[])


@app.get('/testrun/{sha}/status')
def get_status(sha: str, db: Session = Depends(get_db)):
    """
    Private API - called within the cluster by cypress-runner to get the testrun status
    """
    # return 204 if we're still building - the runners can wait
    status = crud.get_test_run_status(db, sha)
    if not status:
        raise HTTPException(404)

    logger.info(f"status={status}")
    return {'status': status}


@app.get('/testrun/{sha}/next')
def get_next_spec(sha: str, db: Session = Depends(get_db)):
    """
    Private API - called within the cluster by cypress-runner to get the next file to test
    """
    spec = crud.get_next_spec_file(db, sha)
    if spec:
        logger.info(f"Returning spec {spec.file} for {sha}")
        return {"spec": spec.file, "id": spec.id}
    raise HTTPException(204)


@app.post('/testrun/{specid}/completed')
async def runner_completed(specid: int, request: Request, db: Session = Depends(get_db)):
    """
    Private API - called within the cluster by cypress-runner when a test spec has completed
    """
    logger.info(f"mark spec completed {specid}")
    spec = crud.mark_completed(db, id)
    # the body will be a tar of the results
    tf = tarfile.TarFile(fileobj=BytesIO(await request.body()))
    tf.extractall(os.path.join(settings.RESULTS_DIR, spec.testrun.sha))

    testrun = spec.testrun
    remaining = crud.get_remaining(db, testrun)
    # has the entire run finished?
    if not remaining:
        logging.info(f"Creating report for {testrun.sha}")

        notify(testrun)

    return "OK"


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
