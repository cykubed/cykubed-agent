import logging
import os
import threading
import time

import click
import requests

import jobs
from build import clone_repos, get_specs, create_build
from settings import settings
from utils import log

running = False


def log_watcher(trid: int, path: str, offset=0):
    global running
    while running:
        with open(path) as f:
            if offset:
                f.seek(offset)
            logs = f.read()
            if logs:
                offset += len(logs)
                r = requests.post(f'{settings.CYKUBE_APP_URL}/hub/logs/{trid}', data=logs)
                if r.status_code != 200:
                    logging.error(f"Failed to push logs: {r.json()}")
        time.sleep(settings.LOG_UPDATE_PERIOD)


@click.command()
@click.argument('trid', help='TestRun ID')
@click.argument('url', help='Clone URL')
@click.argument('sha', help='SHA')
@click.argument('branch', help='Branch')
@click.option('--parallelism', default=None, help="Parallelism override")
def clone_and_build(trid, url, sha, branch,
                    parallelism: int = None):
    """
    Clone and build (from Bitbucket)
    """
    if not parallelism:
        parallelism = settings.PARALLELISM

    logfile_name = os.path.join(settings.DIST_DIR, f'{trid}.log')
    logfile = open(logfile_name, 'w')

    # start log thread
    global running
    running = True
    t = threading.Thread(target=log_watcher, args=(trid, logfile_name))
    t.start()

    t = time.time()
    os.makedirs(settings.DIST_DIR, exist_ok=True)
    os.makedirs(settings.RESULTS_DIR, exist_ok=True)
    os.makedirs(settings.NPM_CACHE_DIR, exist_ok=True)
    try:
        # check for existing dist (for a rerun)
        dist = os.path.join(settings.DIST_DIR, f'{sha}.tgz')
        if os.path.exists(dist):
            # remove it
            os.remove(dist)

        # clone
        wdir = clone_repos(url, branch, logfile)
        # get the list of specs and create a testrun
        specs = get_specs(wdir)
        logfile.write(f"Found {len(specs)} spec files\n")

        if not specs:
            logfile.write("No specs - nothing to test\n")
            # TODO tell cykube
            testrun_to_specs.pop(trid)
            return

        # start the runner jobs - that way the cluster has a head start on spinning up new nodes
        if jobs.batchapi:
            log(logfile, f"Starting {parallelism} Jobs")
            jobs.start_runner_job(branch, sha, logfile, parallelism=parallelism)
        else:
            log(logfile, f"Test mode: sha={sha}")

        # build the distro
        create_build(branch, sha, wdir, logfile)
        t = time.time() - t
        logfile.write(f"Distribution created in {t:.1f}s\n")

    except Exception as ex:
        logfile.write(f"BUILD FAILED: {str(ex)}\n")
        logging.exception("Failed to create build")
        logfile.close()


def start_run(**args):
    # TODO start Job or run inline
    if jobs.batchapi:
        # fire in Job
        jobs.start_clone_job(**args)
    else:
        clone_and_build(**args)


if __name__ == '__main__':
    clone_and_build()
