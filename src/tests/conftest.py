import shutil
import tempfile

import httpx
import pytest
from loguru import logger
from redis.asyncio import Redis as AsyncRedis

import messages
from common.enums import PlatformEnum
from common.schemas import Project, OrganisationSummary, NewTestRun
from common.settings import settings


@pytest.fixture(autouse=True)
def aredis(mocker):
    r = AsyncRedis(host='localhost', db=1, decode_responses=True)
    mocker.patch('db.async_redis', return_value=r)
    return r


@pytest.fixture(autouse=True)
async def init(aredis):
    settings.TEST = True
    settings.REDIS_DB = 1
    settings.REDIS_HOST = 'localhost'
    settings.MESSAGE_POLL_PERIOD = 0.1
    settings.CACHE_DIR = tempfile.mkdtemp()
    logger.remove()
    await messages.queue.init()
    yield
    shutil.rmtree(settings.CACHE_DIR)
    await aredis.flushdb()


@pytest.fixture()
async def mockapp():
    return {'httpclient': httpx.AsyncClient(base_url='http://localhost:5050')}


@pytest.fixture()
async def project() -> Project:
    org = OrganisationSummary(id=5, name='MyOrg')
    return Project(id=10,
                   name='project',
                   default_branch='master',
                   agent_id=1,
                   start_runners_first=False,
                   platform=PlatformEnum.GITHUB,
                   runner_image='cykube-runner:1234',
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
