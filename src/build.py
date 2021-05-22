import hashlib
import logging
import os
import subprocess
import tempfile
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

import crud
from settings import settings
from utils import runcmd

FILES_TO_COPY = ['.npmrc', 'package.json', 'package-lock.json']
ROOT_DIR = os.path.join(os.path.dirname(__file__), '..')


def clone_repos(url: str, branch: str, logfile) -> str:
    logfile.write("Cloning repository\n")
    builddir = tempfile.mkdtemp()
    os.chdir(builddir)
    runcmd(f'git clone --single-branch --depth 1 --recursive --branch {branch} {url} {builddir}', logfile=logfile)
    return builddir


def get_lock_hash(build_dir):
    m = hashlib.sha256()
    lockfile = os.path.join(build_dir, 'package-lock.json')
    # hash the lock
    with open(lockfile, 'rb') as f:
        m.update(f.read())
    return m.hexdigest()


def create_build(db: Session, sha: str, builddir: str, branch: str, logfile):
    """
    Build the Angular app. Uses a cache for node_modules
    """
    logfile.write(f"Creating build distribution for branch {branch}\n")
    os.chdir(builddir)
    lockhash = get_lock_hash(builddir)
    cache_dir = settings.NPM_CACHE_DIR
    os.makedirs(cache_dir, exist_ok=True)

    cached_node_modules_dir = os.path.join(cache_dir, lockhash)
    cache_exists = os.path.exists(cached_node_modules_dir)
    if cache_exists:
        logfile.write("Using npm cache")
        subprocess.check_call(f'cp -ar {cached_node_modules_dir} node_modules', shell=True, stdout=logfile,
                              stderr=logfile)
    else:
        # build node_modules
        logfile.write("Build new npm cache")
        runcmd('npm ci', logfile=logfile)
        # the test runner will need some deps
        runcmd('npm i node-fetch walk-sync uuid @google-cloud/storage sleep-promise mime-types', logfile=logfile)

    # build the app
    logfile.write(f"Building {branch}\n")
    runcmd('./node_modules/.bin/ng build -c ci', logfile=logfile)

    # tar it up
    distdir = settings.DIST_DIR
    os.makedirs(distdir, exist_ok=True)
    logfile.write("Create distribution and cleanup\n")
    # tarball everything - we're running in gigabit ethernet
    subprocess.check_call(f'tar zcf {distdir}/{sha}.tgz ./node_modules ./dist ./src ./cypress *.json *.js',
                          shell=True)

    # mark the spec as 'running'
    crud.mark_as_running(db, sha)

    # update the node cache
    if not cache_exists:
        # store in the cache
        logging.info("Updating cache")
        runcmd(f'cp -fr ./node_modules {cached_node_modules_dir}', logfile)

    # clean up the build, but leave the distribution
    subprocess.check_call(f'rm -fr {builddir}', shell=True, stdout=logfile, stderr=logfile)

    return os.path.join(distdir, f'{sha}.tgz')


def get_specs(wdir):
    specs = []
    for root, dirs, files in os.walk(os.path.join(wdir, 'cypress/integration')):
        for f in files:
            if f.endswith('.ts'):
                p = os.path.join(root, f)[len(wdir)+1:]
                specs.append(p)
    return specs


def delete_old_dists(threshold_hours: int = 12):
    # delete old runs and distributions (along with log files)
    threshold = datetime.utcnow() - timedelta(hours=threshold_hours)
    distdir = settings.DIST_DIR
    for f in os.listdir(distdir):
        path = os.path.join(distdir, f)
        dt = datetime.fromtimestamp(os.stat(path).st_atime)
        if dt < threshold:
            os.remove(path)
#
# if __name__ == '__main__':
#     logs.init()
#     t = time.time()
#     d = clone_repos('git@bitbucket.org:kisanhubcore/kisanhub-webapp.git', 'cypress-runner')
#     dist = create_build('542ae1980c462fee9a2c21ec20f00c6def1359c3', d, 'cypress-runner')
#     t = time.time()-t
#     print(f"Took {t} secs: {dist}")
#
