import json
import logging
import os
import shutil
import tarfile
from datetime import datetime
from io import BytesIO
from typing import List, Any, AnyStr, Dict

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi_auth0 import Auth0
from fastapi_utils.session import FastAPISessionMaker
from fastapi_utils.tasks import repeat_every
from loguru import logger
from sqlalchemy.orm import Session
from starlette.middleware.cors import CORSMiddleware

import crud
import jobs
import schemas
from build import delete_old_dists
from crud import TestRunParams
from models import get_db, TestRun
from notify import notify_failed, notify_fixed
from settings import settings
from worker import app as celeryapp

sessionmaker = FastAPISessionMaker(settings.CYPRESSHUB_DATABASE_URL)
auth = Auth0(domain='khauth.eu.auth0.com', api_audience='https://testhub-api.kisanhub.com')
app = FastAPI(dependencies=[Depends(auth.implicit_scheme)])

origins = [
    'http://localhost:4201',
    'https://cypresshub.kisanhub.com',
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

JSONObject = Dict[AnyStr, Any]

logger.info("Started server")

jobs.connect_k8()


@app.get('/hc')
def health_check(db: Session = Depends(get_db)):
    crud.count_test_runs(db)
    return {'message': 'OK'}


@app.get('/api/testruns', response_model=List[schemas.TestRun])
def get_testruns(page: int = 1, page_size: int = 50,
                db: Session = Depends(get_db)): #, user: Auth0User = Security(auth.get_user)):
    return crud.get_test_runs(db, page, page_size)


@app.get('/api/bitbucket/webhook/{token}/{project}/{repos}')
def bitbucket_webhook(token: str, project: str, repos: str,
                      payload: JSONObject,
                      db: Session = Depends(get_db)):
    if token != settings.BITBUCKET_WEBHOOK_TOKEN:
        raise HTTPException(status_code=403)
    change = payload['push']['changes'][0]['new']
    if change['type'] != 'branch':
        return "no content", 204
    branch = change['name']
    sha = change['target']['hash']

    logging.info(f"Webhook received for {branch} {sha}")
    crud.cancel_previous_test_runs(db, branch)
    celeryapp.send_task('clone_and_build', args=[repos, branch, sha])
    return {'message': 'OK'}


def clear_results(sha: str):
    rdir = os.path.join(settings.RESULTS_DIR, sha)
    shutil.rmtree(rdir, ignore_errors=True)
    os.mkdir(rdir)


@app.post('/api/start')
def start_testrun(params: TestRunParams, db: Session = Depends(get_db)): #, user: Auth0User = Security(auth.get_user)):
    logger.info(f"Start test run {params.repos} {params.branch} {params.sha} {params.parallelism}")
    crud.cancel_previous_test_runs(db, params.sha, params.branch)
    clear_results(params.sha)
    celeryapp.send_task('clone_and_build', args=[params.repos, params.sha, params.branch,
                                                 params.parallelism, params.spec_filter])
    return {'message': 'Test run started'}


@app.post('/api/cancel/{id}')
def cancel_testrun(id: int, db: Session = Depends(get_db)):
    tr = crud.get_testrun(db, id)
    jobs.delete_jobs_for_branch(tr.branch)
    crud.cancel_testrun(db, tr)
    return {'cancelled': 'OK'}


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


@app.post('/testrun/{id}/completed')
async def runner_completed(id: int, request: Request, db: Session = Depends(get_db)):
    """
    Private API - called within the cluster by cypress-runner when a test spec has completed
    """
    logger.info(f"mark_completed {id}")
    spec = crud.mark_completed(db, id)
    # the body will be a tar of the results
    tf = tarfile.TarFile(fileobj=BytesIO(await request.body()))
    tf.extractall(os.path.join(settings.RESULTS_DIR, spec.testrun.sha, 'json', str(id)))

    testrun = spec.testrun
    remaining = crud.get_remaining(db, testrun)
    # has the entire run finished?
    if not remaining:
        logging.info(f"Creating report for {testrun.sha}")

        stats = merge_results(testrun)
        crud.mark_complete(db, testrun, stats['failures'])
        notify(stats, testrun, db)

    return "OK"


def notify(stats, testrun: TestRun, db: Session):
    if stats['failures']:
        notify_failed(testrun, stats['failures'], stats['failed_tests'])
    else:
        # did the last run pass?
        last = crud.get_last_run(db, testrun)
        if last and last.status != 'passed':
            # nope - notify
            notify_fixed(testrun)


def merge_results(testrun: TestRun):
    """
    Merge the mochawesome results and screenshots into a single directory
    """
    sha = testrun.sha
    root = os.path.join(settings.RESULTS_DIR, sha)
    json_root = os.path.join(root, 'json')

    tests = 0
    passes = 0
    fails = 0
    skipped = 0
    pending = 0
    suites = 0
    results = []

    sshots_dir = os.path.join(root, 'screenshots')
    if not os.path.exists(sshots_dir):
        os.mkdir(sshots_dir)

    failed_tests = []

    for d in os.listdir(json_root):
        subd = os.path.join(json_root, d, 'mochawesome-report')
        with open(os.path.join(subd, 'mochawesome.json')) as f:
            report = json.loads(f.read())
            stats = report['stats']
            tests += stats['tests']
            pending += stats['pending']
            passes += stats['passes']
            fails += stats['failures']
            skipped += stats['skipped']
            for result in report['results']:
                for suite in result['suites']:
                    suites += 1
                    for test in suite['tests']:
                        if test['fail']:
                            failed_tests.append({'file': result['file'], 'test': test['title']})

            results.extend(report['results'])

        spec_sshots = os.path.join(subd, 'screenshots')
        if os.path.exists(spec_sshots):
            shutil.copytree(spec_sshots, sshots_dir, dirs_exist_ok=True)
        # shutil.rmtree(subd)

    merged = dict(stats=dict(suites=suites, tests=tests, pending=pending,
                             failures=fails, passes=passes, skipped=skipped,
                             start=testrun.started.isoformat(), end=datetime.now().isoformat(),

                             duration=(datetime.now() - testrun.started).seconds,
                             testsRegistered=tests,
                             passPercent=(passes * 100) / tests,
                             pendingPercent=0,
                             other=0,
                             hasOther=False,
                             hasSkipped=(skipped > 0),
                  failed_tests=failed_tests),
                  results=results)

    with open(os.path.join(root, 'results.json'), 'w') as f:
        f.write(json.dumps(merged, indent=4))

    return merged['stats']


@app.on_event("startup")
@repeat_every(seconds=3000)
def handle_timeouts():

    with sessionmaker.context_session() as db:
        crud.apply_timeouts(db, settings.TEST_RUN_TIMEOUT, settings.SPEC_FILE_TIMEOUT)
    delete_old_dists()

#
#
# @app.route('/testrun/<key>/create_report', methods=['POST'])
# def report(key):
#     logging.info(f"Create report for {key}")
#     create_report(key)
#


