import logging
import os
import subprocess
import tempfile
import threading
import time
from typing import TextIO

import click
import requests

import jobs
import schemas
import testruns
from build import clone_repos, get_specs, create_build
from cykube import cykube_headers
from schemas import NewTestRun
from settings import settings
from utils import log

running = False


def log_watcher(trid: int, fname: str):
    with open(fname, 'r') as logfile:
        global running
        while running:
            logs = logfile.read().encode('utf8')
            if logs:
                r = requests.post(f'{settings.CYKUBE_APP_URL}/hub/logs/{trid}', data=logs,
                                  headers=cykube_headers)
                if r.status_code != 200:
                    logging.error(f"Failed to push logs: {r.json()}")
            time.sleep(settings.LOG_UPDATE_PERIOD)


@click.command()
@click.option('--id', type=int, required=True, help='Testrun ID')
@click.option('--url', type=str, required=True, help='Clone URL')
@click.option('--sha', type=str, required=True, help='SHA')
@click.option('--branch', type=str, required=True, help='Branch')
@click.option('--build_cmd', type=str, default='ng build --output-path=dist', help='NPM build command')
@click.option('--parallelism', type=int, default=None, help="Parallelism override")
def main(id, url, sha, branch, build_cmd, parallelism=None):
    if not parallelism:
        parallelism = settings.PARALLELISM
    tr = NewTestRun(id=id, url=url, sha=sha, branch=branch, parallelism=parallelism, build_cmd=build_cmd)
    clone_and_build(tr)


def post_status(testrun: NewTestRun, status: schemas.Status):
    r = requests.post(f'{settings.HUB_URL}/testrun/{testrun.id}/status/{status}',
                      timeout=10)
    if r.status_code != 200:
        logging.error(f"Failed to contact hub to update status for run {testrun.id}")


def clone_and_build(testrun: NewTestRun):
    """
    Clone and build (from Bitbucket)
    """
    parallelism = testrun.parallelism or settings.PARALLELISM

    logfile = tempfile.NamedTemporaryFile(suffix='.log', mode='w')

    # start log thread
    global running
    running = True
    logthread = threading.Thread(target=log_watcher, args=(testrun.id, logfile.name))
    logthread.start()

    t = time.time()
    post_status(testrun, schemas.Status.building)
    try:
        # clone
        wdir = clone_repos(testrun.url, testrun.branch, logfile)
        # get the list of specs and create a testrun
        specs = get_specs(wdir)
        logfile.write(f"Found {len(specs)} spec files\n")

        if not specs:
            logfile.write("No specs - nothing to test\n")
            post_status(testrun, schemas.Status.passed)
        else:
            requests.put(f'{settings.HUB_URL}/testrun/{testrun.id}/specs',
                            json=specs)

            # start the runner jobs - that way the cluster has a head start on spinning
            # up new nodes
            if jobs.batchapi:
                log(logfile, f"Starting {parallelism} Jobs")
                jobs.start_runner_job(testrun.branch, testrun.sha, logfile,
                                      parallelism=parallelism)
            else:
                log(logfile, f"Test mode: sha={testrun.sha}")

            # build the distro
            create_build(testrun, wdir, logfile)
            post_status(testrun, schemas.Status.running)
            t = time.time() - t
            logfile.write(f"Distribution created in {t:.1f}s\n")

    except Exception as ex:
        logfile.write(f"BUILD FAILED: {str(ex)}\n")
        logging.exception("Failed to create build")
    finally:
        running = False
        logthread.join()
        logfile.close()


def start_run(newrun: NewTestRun):
    testruns.add_run(newrun)

    if jobs.batchapi:
        # fire in Job
        jobs.start_clone_job(newrun)
    else:
        # test mode - use a thread
        clone_thread = threading.Thread(target=clone_and_build, args=(newrun,))
        clone_thread.start()


if __name__ == '__main__':
    main()
