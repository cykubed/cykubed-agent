import asyncio
import base64
import logging
import subprocess
import sys
import tempfile
import threading
import time

import requests

import jobs
from build import clone_repos, create_node_environment, get_specs, build_app
from common.enums import TestRunStatus
from common.schemas import NewTestRun
from settings import settings
from utils import log

running = False
cykube_headers = {'Authorization': f'Bearer {settings.API_TOKEN}',
                  'Accept': 'application/json'}


def log_watcher(trid: int, fname: str):
    with open(fname, 'r') as logfile:
        global running
        while running:
            logs = logfile.read()
            if logs:
                print(logs)
                r = requests.post(f'{settings.CYKUBE_API_URL}/agent/testrun/{trid}/logs', data=logs.encode('utf8'),
                                  headers=cykube_headers)
                if r.status_code != 200:
                    logging.error(f"Failed to push logs")
            time.sleep(settings.LOG_UPDATE_PERIOD)


def post_status(testrun: NewTestRun, status: TestRunStatus):
    r = requests.put(f'{settings.CYKUBE_API_URL}/agent/testrun/{testrun.id}/status',
                      headers=cykube_headers,
                      timeout=10, json={'status': status.name})
    if r.status_code != 200:
        logging.error(f"Failed to contact CyKube to update status for run {testrun.id}")


async def clone_and_build(testrun: NewTestRun):
    """
    Clone and build
    """
    parallelism = testrun.project.parallelism

    logfile = tempfile.NamedTemporaryFile(suffix='.log', mode='w')

    # start log thread
    global running
    running = True
    logthread = threading.Thread(target=log_watcher, args=(testrun.id, logfile.name))
    logthread.start()

    t = time.time()
    try:
        # clone
        wdir = clone_repos(testrun.project.url, testrun.branch, logfile)

        if not testrun.sha:
            testrun.sha = subprocess.check_output(['git', 'rev-parse',  testrun.branch], cwd=wdir,
                                                  text=True).strip('\n')

        # install node packages first (or fetch from cache)
        await create_node_environment(testrun, wdir, logfile)

        # now we can determine the specs
        specs = get_specs(wdir)

        # tell cykube
        r = requests.put(f'{settings.CYKUBE_API_URL}/agent/testrun/{testrun.id}/specs',
                         headers=cykube_headers,
                         json={'specs': specs, 'sha': testrun.sha})
        if not r.status_code == 200:
            logfile.write("Failed to update cykube with list of specs - bailing out")
            return

        # start the runner jobs - that way the cluster has a head start on spinning
        # up new nodes
        if settings.JOB_MODE == 'k8':
            log(logfile, f"Starting {parallelism} Jobs")
            jobs.create_runner_jobs(testrun)
        else:
            log(logfile, f"Test mode: sha={testrun.sha}")

        # build the app
        await build_app(testrun, wdir, logfile)

        if not specs:
            logfile.write("No specs - nothing to test\n")
            post_status(testrun, TestRunStatus.passed)

        logfile.write(f"Found {len(specs)} spec files\n")
        post_status(testrun, TestRunStatus.running)
        t = time.time() - t
        logfile.write(f"Distribution created in {t:.1f}s\n")

    except Exception as ex:
        logfile.write(f"BUILD FAILED: {str(ex)}\n")
        logging.exception("Failed to create build")
    finally:
        running = False
        logthread.join()
        logfile.close()


async def start_run(newrun: NewTestRun):
    if settings.JOB_MODE == 'k8':
        # stop existing jobs
        jobs.delete_jobs_for_branch(newrun.branch)
        # and create a new one
        jobs.create_build_job(newrun)
    else:
        # inline mode (for testing)
        await clone_and_build(newrun)


if __name__ == '__main__':
    trjson = base64.b64decode(sys.argv[1])
    tr = NewTestRun.parse_raw(trjson)
    asyncio.run(clone_and_build(tr))

