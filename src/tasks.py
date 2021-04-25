import logging
import shutil
import time

from celery import Celery
from fastapi_utils.session import FastAPISessionMaker

import crud
import jobs
import logs
from build import clone_repos, get_specs, create_build
from crud import TestRunParams
from integration import get_commit_info
from settings import settings

sessionmaker = FastAPISessionMaker(settings.CYPRESSHUB_DATABASE_URL)

from worker import app

logs.init()


@app.task
def ping():
    logging.info("ping called")
    try:
        with sessionmaker.context_session() as db:
            logging.info(f'{crud.count_test_runs(db)} test runs')
    except:
        logging.exception("Failed")


@app.task
def clone_and_build(repos: str, sha: str, branch: str):
    with sessionmaker.context_session() as db:
        t = time.time()
        wdir = None
        try:
            # clone
            wdir = clone_repos(f'{settings.BITBUCKET_APP_PASSWORD}/{repos}.git', branch)
            # get the list of specs and create a testrun
            specs = get_specs(wdir)
            info = get_commit_info(repos, sha)
            crud.create_testrun(db, TestRunParams(repos=repos, sha=sha, branch=branch),
                                specs, **info)

            # start the runner jobs - that way the cluster has a head start on spinning up new nodes
            if jobs.batchapi:
                jobs.start_job(branch, sha)

            # build the distro
            create_build(db, sha, wdir, branch)
            t = time.time() - t
            logging.info(f"Build created in {t:.1f}s")
        except:
            logging.exception("Failed to create build")
        finally:
            if wdir:
                shutil.rmtree(wdir)
