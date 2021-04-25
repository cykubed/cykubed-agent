import logging
from typing import List, Any, AnyStr, Dict

from fastapi import Depends, FastAPI, HTTPException
from fastapi_auth0 import Auth0
from fastapi_utils.session import FastAPISessionMaker
from fastapi_utils.tasks import repeat_every
from sqlalchemy.orm import Session

import crud
import logs
import schemas
from build import delete_old_dists
from crud import TestRunParams
from models import get_db
from notify import notify_failed, notify_fixed
from report import create_report
from settings import settings
from worker import app as celeryapp

sessionmaker = FastAPISessionMaker(settings.CYPRESSHUB_DATABASE_URL)
auth = Auth0(domain='khauth.eu.auth0.com', api_audience='https://testhub-api.kisanhub.com')
app = FastAPI()

JSONObject = Dict[AnyStr, Any]

logs.init()


@app.get('/hc')
def health_check(db: Session = Depends(get_db)):
    crud.count_test_runs(db)
    return {'message': 'OK'}


@app.get('/api/testruns', response_model=List[schemas.TestRun], dependencies=[Depends(auth.implicit_scheme)])
def get_testruns(page: int = 1, page_size: int = 50, db: Session = Depends(get_db),
                 dependencies=[Depends(auth.implicit_scheme)]):
    logging.info("Test - /api/testruns")
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
    params = TestRunParams(branch=branch, sha=sha, repos=repos)
    params.repos = f'{project}/{repos}'

    celeryapp.send_task('clone_and_build', args=[repos, branch, sha])
    return {'message': 'OK'}


@app.post('/api/start')
def start_testrun(params: TestRunParams, db: Session = Depends(get_db),
                  dependencies=[Depends(auth.implicit_scheme)]):
    crud.cancel_previous_test_runs(db, params.branch)
    celeryapp.send_task('clone_and_build', args=[params.repos, params.branch, params.sha])
    return {'message': 'Test run started'}


@app.get('/testrun/{sha}/status')
def get_status(sha: str, db: Session = Depends(get_db)):
    """
    Private API - called within the cluster by cypress-runner to get the testrun status
    """
    # return 204 if we're still building - the runners can wait
    tr = crud.get_test_run_status(db, sha)
    if not tr:
        raise HTTPException(404)

    return {'status': str(tr.status).lower()}


@app.get('/testrun/{sha}/next')
def get_next_spec(sha: str, db: Session = Depends(get_db)):
    """
    Private API - called within the cluster by cypress-runner to get the next file to test
    """
    spec = crud.get_next_spec_file(db, sha)
    if spec:
        logging.info(f"Returning spec {spec.file} for {sha}")
        return {"spec": spec.file, "id": spec.id}
    raise HTTPException(204)


@app.post('/testrun/{id}/completed')
def runner_completed(id: int, db: Session = Depends(get_db)):
    """
    Private API - called within the cluster by cypress-runner when a test spec has completed
    """
    spec = crud.mark_completed(db, id)
    testrun = spec.testrun
    remaining = crud.get_remaining(db, testrun)
    # has the entire run finished?
    if not remaining:
        logging.info(f"Creating report for {testrun.sha}")
        total_fails, specs_with_fails = create_report(testrun.sha, testrun.branch)

        if total_fails:
            notify_failed(testrun, total_fails, specs_with_fails)
        else:
            # did the last run pass?
            last = crud.get_last_run(db, testrun)
            if last and last.status != 'passed':
                # nope - notify
                notify_fixed(testrun)

    return "OK"


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


