import logging
import os
import re
import time

import requests
from fastapi_utils.session import FastAPISessionMaker

import crud
import jobs
from build import clone_repos, get_specs, create_build
from settings import settings
from utils import log

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


@app.task(name='upload_log')
def log_watcher(trid: int, path: str, offset=0):
    with sessionmaker.context_session() as db:
        tr = crud.get_testrun(db, trid)
        if tr.active:
            with open(path) as f:
                if offset:
                    f.seek(offset)
                logs = f.read()
                if logs:
                    offset += len(logs)
                    r = requests.post(f'{settings.CYKUBE_APP_URL}/hub/logs/{trid}', data=logs)
                    if r.status_code != 200:
                        logging.error(f"Failed to push logs: {r.json()}")
                # schedule another task
                log_watcher.apply_async(args=(trid, path), countdown=settings.LOG_UPDATE_PERIOD)


@app.task(name='clone_and_build')
def clone_and_build(trid: int, parallelism: int = None,
                    spec_filter: str = None):
    """
    Clone and build (from Bitbucket)
    """
    if not parallelism:
        parallelism = settings.PARALLELISM
    with sessionmaker.context_session() as db:

        logfile_name = os.path.join(settings.DIST_DIR, f'{trid}.log')
        logfile = open(logfile_name, 'w')

        # start a log watcher
        log_watcher.delay(trid, logfile)

        tr = crud.get_testrun(db, trid)
        sha = tr.sha
        branch = tr.branch

        t = time.time()
        os.makedirs(settings.DIST_DIR, exist_ok=True)
        os.makedirs(settings.RESULTS_DIR, exist_ok=True)
        os.makedirs(settings.NPM_CACHE_DIR, exist_ok=True)
        try:
            # check for existing dist (for a rerun)
            dist = os.path.join(settings.DIST_DIR, f'{sha}.tgz')
            specs = None
            if os.path.exists(dist):
                # we'll have a previous run - use that for the specs
                log(logfile, "Using existing distribution")
                specs = crud.get_last_specs(db, sha)

            if specs is None:
                # clone
                wdir = clone_repos(tr.repos, branch, logfile)
                # get the list of specs and create a testrun
                specs = get_specs(wdir)
                logfile.write(f"Found {len(specs)} spec files\n")

            # filter the specs through the glob
            if spec_filter:
                try:
                    filter_compiled = re.compile(spec_filter)
                    specs = [spec for spec in specs if filter_compiled.match(spec)]
                except re.error:
                    logfile.write(f"Invalid filter {spec_filter}: ignoring")

            if not specs:
                logfile.write("No specs - nothing to test\n")
                return

            crud.update_test_run(db, tr, specs)

            # start the runner jobs - that way the cluster has a head start on spinning up new nodes
            if jobs.batchapi:
                log(logfile, f"Starting {parallelism} Jobs")
                jobs.start_job(branch, sha, logfile, parallelism=parallelism)

            # build the distro
            if not os.path.exists(dist):
                create_build(db, tr, wdir, logfile)
                t = time.time() - t
                logfile.write(f"Distribution created in {t:.1f}s\n")
            else:
                logfile.write(f"Distribution already exists: reuse it\n")
                crud.mark_as_running(db, tr)
        except Exception as ex:
            logfile.write(f"BUILD FAILED: {str(ex)}\n")
            logging.exception("Failed to create build")
            logfile.close()

