import pytest
from loguru import logger
from redis import Redis
from redis.asyncio import Redis as AsyncRedis

from common.enums import PlatformEnum
from common.schemas import Project, OrganisationSummary, NewTestRun


@pytest.fixture()
def redis(mocker, autouse=True):
    r = Redis(host='localhost', db=1, decode_responses=True)
    r.flushdb()
    aredis = AsyncRedis(host='localhost', db=1, decode_responses=True)
    mocker.patch('db.async_redis', return_value=aredis)
    mocker.patch('ws.async_redis', return_value=aredis)
    logger.remove()
    return r


@pytest.fixture()
def mock_create_from_yaml(mocker):
    mocker.patch('jobs.client')
    return mocker.patch('jobs.k8utils.create_from_yaml')


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
def k8_core_api_mock(mocker):
    core_api_mock = mocker.MagicMock()
    mocker.patch('jobs.get_core_api', return_value=core_api_mock)
    return core_api_mock


@pytest.fixture()
def testrun(project: Project) -> NewTestRun:
    return NewTestRun(url='git@github.org/dummy.git',
                      id=20,
                      sha='deadbeef0101',
                      local_id=1,
                      project=project,
                      status='started',
                      branch='master')
