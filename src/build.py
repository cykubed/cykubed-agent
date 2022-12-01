import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from shutil import copyfileobj

import requests

from common.schemas import NewTestRun
from exceptions import BuildFailedException
from settings import settings
from utils import runcmd


def clone_repos(url: str, branch: str, logfile) -> str:
    logfile.write("Cloning repository\n")
    builddir = tempfile.mkdtemp()
    os.chdir(builddir)
    runcmd(f'git clone --single-branch --depth 1 --recursive --branch {branch} {url} {builddir}', logfile=logfile)
    logfile.write("Cloned\n")
    return builddir


def get_lock_hash(build_dir):
    m = hashlib.sha256()
    lockfile = os.path.join(build_dir, 'package-lock.json')
    if not os.path.exists(lockfile):
        lockfile = os.path.join(build_dir, 'yarn.lock')

    if not os.path.exists(lockfile):
        raise BuildFailedException("No lock file")

    # hash the lock
    with open(lockfile, 'rb') as f:
        m.update(f.read())
    return m.hexdigest()


def upload_to_cache(filename, file):
    r = requests.post(os.path.join(settings.HUB_URL, 'upload'), files={
        'file': (filename, file, 'application/octet-stream')
    })
    r.raise_for_status()


def create_build(testrun: NewTestRun, builddir: str, logfile):
    """
    Build the app. Uses a cache for node_modules
    """
    branch = testrun.branch
    sha = testrun.sha

    logfile.write(f"Creating build distribution for branch {branch} in dir {builddir}\n")
    os.chdir(builddir)
    lockhash = get_lock_hash(builddir)

    cache_filename = f'{lockhash}.tar.lz4'
    with requests.get(os.path.join(settings.CACHE_URL, cache_filename), stream=True) as resp:
        if resp.status_code == 200:
            with tempfile.NamedTemporaryFile() as fdst:
                copyfileobj(resp.raw, fdst)
                # unpack
                logfile.write("Fetching npm cache\n")
                runcmd(f'tar xf {fdst.name} -I lz4', logfile=logfile)
        else:
            # build node_modules
            logfile.write("Build new npm cache\n")
            if os.path.exists('yarn.lock'):
                runcmd('yarn install --pure-lockfile', logfile=logfile)
            else:
                runcmd('npm ci', logfile=logfile)

            # tar up and store
            with tempfile.NamedTemporaryFile(suffix='.tar.lz4') as fdst:
                runcmd(f'tar cf {fdst.name} -I lz4 node_modules', logfile=logfile)
                # upload
                upload_to_cache(cache_filename, fdst)

    # build the app
    logfile.write(f"Building {branch}\n")
    runcmd(f'./node_modules/.bin/{testrun.project.build_cmd}', logfile=logfile)

    # tar it up
    with tempfile.NamedTemporaryFile(suffix='.tar.lz4') as fdst:
        logfile.write("Create distribution and cleanup\n")
        # tarball everything
        runcmd(f'tar cf {fdst.name} . -I lz4', logfile=logfile)
        # and upload
        upload_to_cache(f'{sha}.tar.lz4', fdst)


def get_specs(wdir):
    cyjson = os.path.join(wdir, 'cypress.json')

    compiled = None

    if not os.path.exists(cyjson):
        # we need to compile the TS config file
        cfg = os.path.join(wdir, 'cypress.config.js')
        if not os.path.exists(cfg):
            cfg = os.path.join(wdir, 'cypress.config.ts')
            if not os.path.exists(cfg):
                raise BuildFailedException("Cannot find Cypress config file")
            # compile it
            subprocess.check_call(['npx', 'tsc', 'cypress.config.ts'], cwd=wdir)
            compiled = os.path.join(wdir, 'cypress.config.js')

    # extract paths
    shutil.copy(os.path.join(os.path.dirname(__file__), 'node/get_specs.mjs'), wdir)
    proc = subprocess.run(['/usr/bin/node', 'get_specs.mjs'], check=True, cwd=wdir, capture_output=True)
    specs = json.loads(proc.stdout.decode())
    if compiled:
        os.remove(compiled)
    os.remove(os.path.join(wdir, 'get_specs.mjs'))
    return specs

