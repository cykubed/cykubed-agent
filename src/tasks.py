import logging
import os
import re
import time

from fastapi_utils.session import FastAPISessionMaker

import crud
import jobs
from build import clone_repos, get_specs, create_build
from integration.common import get_commit_details
from models import PlatformEnum
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


@app.task(name='clone_and_build')
def clone_and_build(trid: int, parallelism: int = None,
                    spec_filter: str = None):
    """
    Clone and build (from Bitbucket)
    """
    if not parallelism:
        parallelism = 4
    with sessionmaker.context_session() as db:

        bitbucket_auth = crud.get_platform_settings(db, PlatformEnum.BITBUCKET)

        logfile_name = os.path.join(settings.DIST_DIR, f'{trid}.log')
        logfile = open(logfile_name, 'w')

        tr = crud.get_testrun(db, trid)
        sha = tr.sha
        branch = tr.branch
        repos = tr.repos

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
                if bitbucket_auth:
                    url = f'https://{bitbucket_auth.username}:{bitbucket_auth.token}@bitbucket.org/{repos}.git'
                else:
                    raise Exception("Only Bitbucket is supported at the moment")
                wdir = clone_repos(url, branch, logfile)
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

            if bitbucket_auth:
                info = get_commit_details(repos, branch, sha)
            else:
                info = {}

            crud.update_test_run(db, tr, specs, **info)

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

