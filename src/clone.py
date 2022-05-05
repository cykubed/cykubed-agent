import logging
import os
import threading
import time

import click
import requests
from fastapi_utils.session import FastAPISessionMaker

import crud
import jobs
from build import clone_repos, get_specs, create_build
from notify import notify
from schemas import Results
from settings import settings
from utils import log, now

sessionmaker = FastAPISessionMaker(settings.CYPRESSHUB_DATABASE_URL)
jobs.connect_k8()
os.makedirs(settings.DIST_DIR, exist_ok=True)
os.makedirs(settings.NPM_CACHE_DIR, exist_ok=True)
os.makedirs(settings.RESULTS_DIR, exist_ok=True)


def log_watcher(trid: int, path: str, offset=0):
    with sessionmaker.context_session() as db:
        while True:
            with open(path) as f:
                if offset:
                    f.seek(offset)
                logs = f.read()
                if logs:
                    offset += len(logs)
                    r = requests.post(f'{settings.CYKUBE_APP_URL}/hub/logs/{trid}', data=logs)
                    if r.status_code != 200:
                        logging.error(f"Failed to push logs: {r.json()}")
            tr = crud.get_testrun(db, trid)
            if not tr.active:
                return
            time.sleep(settings.LOG_UPDATE_PERIOD)


@click.command()
@click.argument('trid', help='Test run ID')
@click.option('--parallelism', default=None, help="Parallelism override")
def clone_and_build(trid: int,
                    parallelism: int = None):
    """
    Clone and build (from Bitbucket)
    """
    if not parallelism:
        parallelism = settings.PARALLELISM

    with sessionmaker.context_session() as db:

        logfile_name = os.path.join(settings.DIST_DIR, f'{trid}.log')
        logfile = open(logfile_name, 'w')

        # start log thread
        t = threading.Thread(target=log_watcher, args=(trid, logfile_name))
        t.start()

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

            if not specs:
                logfile.write("No specs - nothing to test\n")
                tr.status = 'passed'
                tr.active = False
                tr.finished = now()

            crud.update_test_run(db, tr, specs)

            if not specs:
                notify(Results(tr), db)
                return

            # start the runner jobs - that way the cluster has a head start on spinning up new nodes
            if jobs.batchapi:
                log(logfile, f"Starting {parallelism} Jobs")
                jobs.start_runner_job(branch, sha, logfile, parallelism=parallelism)
            else:
                log(logfile, f"Test mode: sha={sha}")

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


def start_run(trid: int,
              parallelism: int = None):
    # TODO start Job or run inline
    if jobs.batchapi:
        # fire in Job
        jobs.start_clone_job(trid)
    else:
        clone_and_build(trid, parallelism)


if __name__ == '__main__':
    clone_and_build()
