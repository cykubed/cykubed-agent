import shutil
import tempfile

import pytest
from loguru import logger

import messages
from common.enums import PlatformEnum
from common.mongo import client
from common.schemas import Project, OrganisationSummary, NewTestRun
from common.settings import settings


@pytest.fixture(autouse=True)
async def init():
    settings.TEST = True
    settings.CYKUBE_CACHE_DIR = tempfile.mkdtemp()
    await client().drop_database(settings.MONGO_DATABASE)
    logger.remove()
    await messages.queue.init()
    yield
    shutil.rmtree(settings.CYKUBE_CACHE_DIR)


@pytest.fixture()
async def project() -> Project:
    org = OrganisationSummary(id=5, name='MyOrg')
    return Project(id=10,
                   name='project',
                   default_branch='master',
                   agent_id=1,
                   start_runners_first=False,
                   platform=PlatformEnum.GITHUB,
                   url='git@github.org/dummy.git',
                   organisation=org)


@pytest.fixture()
async def testrun(project: Project) -> NewTestRun:
    return NewTestRun(url='git@github.org/dummy.git',
                      id=20,
                      local_id=1,
                      project=project,
                      status='started',
                      branch='master')
