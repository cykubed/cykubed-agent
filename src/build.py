import hashlib
import os
import subprocess
import tempfile
from shutil import copyfileobj

import requests

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
    # hash the lock
    with open(lockfile, 'rb') as f:
        m.update(f.read())
    return m.hexdigest()


def create_build(branch: str, sha: str, builddir: str, logfile):
    """
    Build the Angular app. Uses a cache for node_modules
    """
    logfile.write(f"Creating build distribution for branch {branch} in dir {builddir}\n")
    os.chdir(builddir)
    lockhash = get_lock_hash(builddir)

    cache_filename = f'{lockhash}.tar.lz4'
    with requests.get(os.path.join(settings.HUB_URL, 'cache', cache_filename), stream=True) as resp:
        if resp.status_code == 200:
            with tempfile.NamedTemporaryFile() as fdst:
                copyfileobj(resp.raw, fdst)
                # unpack
                logfile.write("Fetching npm cache\n")
                subprocess.check_call(f'tar xf {fdst.name}')
        else:
            # build node_modules
            logfile.write("Build new npm cache\n")
            runcmd('npm ci', logfile=logfile)
            # the test runner will need some deps
            runcmd('npm i node-fetch walk-sync uuid sleep-promise mime-types', logfile=logfile)
            with tempfile.NamedTemporaryFile(suffix='.tar.lz4') as fdst:
                subprocess.check_call(f'tar cf {fdst.name} -I lz4 node_modules')
                # upload
                r = requests.post(os.path.join(settings.HUB_URL, 'cache'), files={
                    'file': (cache_filename, fdst, 'application/octet-stream')
                })
                r.raise_for_status()

    # build the app
    logfile.write(f"Building {branch}\n")
    runcmd('./node_modules/.bin/ng build -c ci --output-path=dist', logfile=logfile)

    # tar it up
    with tempfile.NamedTemporaryFile(suffix='.tar.lz4') as fdst:
        logfile.write("Create distribution and cleanup\n")
        # tarball everything
        subprocess.check_call(f'tar cf {fdst.name} ./node_modules ./dist ./src ./cypress *.json *.js -I lz4',
                              shell=True)
        # and upload
        cache_filename = f'{sha}.tar.lz4'
        r = requests.post(os.path.join(settings.HUB_URL, 'upload', 'cache'), files={
            'file': (cache_filename, fdst, 'application/octet-stream')
        })
        r.raise_for_status()


def get_specs(wdir):
    specs = []
    for root, dirs, files in os.walk(os.path.join(wdir, 'cypress/integration')):
        for f in files:
            if f.endswith('.ts'):
                p = os.path.join(root, f)[len(wdir)+1:]
                specs.append(p)
    return specs

