import json
import logging
import os
import shutil
import tarfile
from io import BytesIO
from typing import List, Any, AnyStr, Dict

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi_auth0 import Auth0
from fastapi_cloudauth import FirebaseCurrentUser
from fastapi_cloudauth.firebase import FirebaseClaims
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
from notify import notify
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

get_current_user = FirebaseCurrentUser(
    project_id=settings.FIREBASE_PROJECT_ID
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
                db: Session = Depends(get_db), user: FirebaseClaims = Depends(get_current_user)):
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
    os.makedirs(rdir, exist_ok=True)
    shutil.rmtree(rdir, ignore_errors=True)
    os.mkdir(rdir)


@app.post('/api/start')
def start_testrun(params: TestRunParams, db: Session = Depends(get_db),
                  user: FirebaseClaims = Depends(get_current_user)):
    logger.info(f"Start test run {params.repos} {params.branch} {params.sha} {params.parallelism}")
    crud.cancel_previous_test_runs(db, params.sha, params.branch)
    clear_results(params.sha)
    celeryapp.send_task('clone_and_build', args=[params.repos, params.sha, params.branch,
                                                 params.parallelism, params.spec_filter])
    return {'message': 'Test run started'}


@app.post('/api/cancel/{id}')
def cancel_testrun(id: int, db: Session = Depends(get_db),
                   user: FirebaseClaims = Depends(get_current_user)):
    tr = crud.get_testrun(db, id)
    jobs.delete_jobs_for_branch(tr.branch)
    crud.cancel_testrun(db, tr)
    return {'cancelled': 'OK'}


@app.get('/api/testrun/{id}/result')
def get_testrun_result(id: int,
                       db: Session = Depends(get_db),
                       user: FirebaseClaims = Depends(get_current_user)
                       ) -> schemas.Results:
    tr = crud.get_testrun(db, id)
    json_result = os.path.join(settings.RESULTS_DIR, tr.sha, 'json', 'results.json')
    if os.path.exists(json_result):
        result: schemas.Results = schemas.Results.parse_file(json_result)
        result.testrun = tr
        return result
    return schemas.Results(testrun=tr, specs=[])


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
    tf.extractall(os.path.join(settings.RESULTS_DIR, spec.testrun.sha))

    testrun = spec.testrun
    remaining = crud.get_remaining(db, testrun)
    # has the entire run finished?
    if not remaining:
        logging.info(f"Creating report for {testrun.sha}")

        stats = merge_results(testrun)
        crud.mark_complete(db, testrun, stats.failures)
        notify(stats, db)

    return "OK"


def create_file_path(sha: str, path: str) -> str:
    i = path.find('build/results/')
    return f'{settings.RESULT_URL}/{sha}/{path[i+14:]}'


def merge_results(testrun: TestRun) -> schemas.Results:
    """
    Merge the results and screenshots into a single directory
    """
    sha = testrun.sha
    results_root = os.path.join(settings.RESULTS_DIR, sha)
    json_root = os.path.join(results_root, 'json')

    results = schemas.Results(testrun=testrun, specs=[])

    for subd in os.listdir(json_root):
        with open(os.path.join(json_root, subd, 'result.json')) as f:
            report = json.loads(f.read())
            run = report['runs'][-1]
            spec_result = schemas.SpecResult(file=run['spec']['name'], results=[])
            results.specs.append(spec_result)
            for test in run['tests']:
                results.total += 1
                attempt = test['attempts'][-1]

                if test['state'] == 'pending':
                    results.skipped += 1
                    startedAt = None
                    duration = None
                else:
                    startedAt = test['attempts'][0]['startedAt']
                    duration = attempt['duration']
                test_result = schemas.TestResult(title=test['title'][1],
                                                 failed=(test['state'] == 'failed'),
                                                 body=test['body'],
                                                 display_error=test['displayError'],
                                                 duration=duration,
                                                 num_attempts=len(test['attempts']),
                                                 started_at=startedAt)
                spec_result.results.append(test_result)

                if not test_result.failed:
                    results.passes += 1
                else:
                    results.failures += 1
                    err = attempt['error']
                    frame = err['codeFrame']
                    code_frame = schemas.CodeFrame(line=frame['line'],
                                                   file=frame['relativeFile'],
                                                   column=frame['column'],
                                                   frame=frame['frame'])

                    test_result.error = schemas.TestResultError(
                        name=err['name'],
                        message=err['message'],
                        stack=err['stack'],
                        screenshots=[create_file_path(sha, ss['path']) for ss in attempt.get('screenshots', [])],
                        videos=[create_file_path(sha, ss['path']) for ss in attempt.get('videos', [])],
                        code_frame=code_frame)

    with open(os.path.join(json_root, 'results.json'), 'w') as f:
        f.write(results.json(indent=4))

    return results


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


