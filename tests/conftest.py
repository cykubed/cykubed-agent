import httpx
import pytest
from loguru import logger
from redis import Redis
from redis.asyncio import Redis as AsyncRedis

from common.enums import PlatformEnum
from common.schemas import Project, OrganisationSummary, NewTestRun


@pytest.fixture()
def redis(mocker):
    r = Redis(host='localhost', db=1, decode_responses=True)
    r.flushdb()
    aredis = AsyncRedis(host='localhost', db=1, decode_responses=True)
    mocker.patch('db.async_redis', return_value=aredis)
    mocker.patch('ws.async_redis', return_value=aredis)
    return r


@pytest.fixture(autouse=True)
async def init(mocker, redis):
    mocker.patch('common.redisutils.RedisSettings.REDIS_DB', return_value=1)
    logger.remove()


@pytest.fixture()
async def mockapp():
    return {'platform': 'GKE',
            'httpclient': httpx.AsyncClient(base_url='http://localhost:5050')}


@pytest.fixture()
async def project() -> Project:
    org = OrganisationSummary(id=5, name='MyOrg')
    return Project(id=10,
                   name='project',
                   default_branch='master',
                   agent_id=1,
                   start_runners_first=False,
                   platform=PlatformEnum.GITHUB,
                   runner_image='cykubed-runner:1234',
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
