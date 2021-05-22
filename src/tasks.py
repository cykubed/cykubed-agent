import logging
import os
import time

from fastapi_utils.session import FastAPISessionMaker

import crud
import jobs
from build import clone_repos, get_specs, create_build
from crud import TestRunParams
from integration import get_commit_info
from settings import settings

sessionmaker = FastAPISessionMaker(settings.CYPRESSHUB_DATABASE_URL)

from worker import app


@app.task(name='ping')
def ping():
    logging.info("ping called")
    try:
        with sessionmaker.context_session() as db:
            logging.info(f'{crud.count_test_runs(db)} test runs')
    except:
        logging.exception("Failed")


@app.task(name='clone_and_build')
def clone_and_build(repos: str, sha: str, branch: str, parallelism: int = None):
    """
    Clone and build (from Bitbucket)
    """
    with sessionmaker.context_session() as db:
        # cancel previous runs
        crud.cancel_previous_test_runs(db, sha, branch)

        t = time.time()
        try:
            logfile = open(os.path.join(settings.DIST_DIR, f'{sha}.log'), 'w')

            # clone
            logging.info(f"Logfile = {logfile.name}")
            wdir = clone_repos(f'https://{settings.BITBUCKET_USERNAME}:{settings.BITBUCKET_APP_PASSWORD}@bitbucket.org/{repos}.git', branch, logfile)
            # get the list of specs and create a testrun
            specs = get_specs(wdir)
            info = get_commit_info(repos, sha)
            crud.create_testrun(db, TestRunParams(repos=repos, sha=sha, branch=branch),
                                specs, **info)

            # start the runner jobs - that way the cluster has a head start on spinning up new nodes
            if jobs.batchapi:
                jobs.start_job(branch, sha, logfile, parallelism=parallelism)

            # build the distro
            create_build(db, sha, wdir, branch, logfile)
            t = time.time() - t
            logfile.write(f"Distribution created in {t:.1f}s\n")
        except Exception as ex:
            logfile.write(f"BUILD FAILED: {str(ex)}\n")
            logging.exception("Failed to create build")

