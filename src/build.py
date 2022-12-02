import hashlib
import json
import os
import re
import shutil
import tempfile
from shutil import copyfileobj

import aiohttp
import requests
from wcmatch import glob

from common.schemas import NewTestRun
from exceptions import BuildFailedException
from settings import settings
from utils import runcmd

INCLUDE_SPEC_REGEX = re.compile(r'specPattern:\s*[\"\'](.*)[\"\']')
EXCLUDE_SPEC_REGEX = re.compile(r'excludeSpecPattern:\s*[\"\'](.*)[\"\']')


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


async def upload_to_cache(file_name, logfile):
    logfile.write("Uploading environment to cache")
    async with aiohttp.ClientSession(base_url=settings.HUB_URL) as session:
        await session.post('/upload', data={'file': open(file_name, 'rb')})
    logfile.write("Environment uploaded")


async def create_node_environment(testrun: NewTestRun, builddir: str, logfile):
    """
    Build the app. Uses a cache for node_modules
    """
    branch = testrun.branch

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
            runcmd(f'tar cf /tmp/{cache_filename} -I lz4 node_modules', logfile=logfile)
            # upload
            await upload_to_cache(f'/tmp/{cache_filename}', logfile)


async def build_app(testrun: NewTestRun, logfile):
    # build the app
    logfile.write(f"Building app\n")
    runcmd(f'./node_modules/.bin/{testrun.project.build_cmd}', logfile=logfile)

    # tar it up
    logfile.write("Create distribution and cleanup\n")
    filename = f'{testrun.sha}.tar.lz4'
    # tarball everything
    runcmd(f'tar cf /tmp/{filename} . -I lz4', logfile=logfile)
    # and upload
    await upload_to_cache(f'/tmp/{filename}', logfile)


def make_array(x):
    if not type(x) is list:
        return [x]
    return x


def get_specs(wdir):
    cyjson = os.path.join(wdir, 'cypress.json')

    tscdir = None

    if os.path.exists(cyjson):
        with open(cyjson, 'r') as f:
            config = json.loads(f.read())
        folder = config.get('integrationFolder', 'cypress/integration')
        include_globs = make_array(config.get('testFiles', '**/*.*'))
        exclude_globs = make_array(config.get('ignoreTestFiles', '*.hot-update.js'))
    else:
        # technically I should use node to extra the various globs, but it's more trouble than it's worth
        # so i'll stick with regex
        folder = ""
        config = os.path.join(wdir, 'cypress.config.js')
        if not os.path.exists(config):
            config = os.path.join(wdir, 'cypress.config.ts')
            if not os.path.exists(config):
                raise BuildFailedException("Cannot find Cypress config file")
        with open(config, 'r') as f:
            cfgtext = f.read()
            include_globs = re.findall(INCLUDE_SPEC_REGEX, cfgtext)
            exclude_globs = re.findall(EXCLUDE_SPEC_REGEX, cfgtext)

    specs = glob.glob(include_globs, root_dir=os.path.join(wdir, folder),
                      flags=glob.BRACE, exclude=exclude_globs)

    if tscdir:
        shutil.rmtree(tscdir)

    specs = [os.path.join(folder, s) for s in specs]
    return specs

