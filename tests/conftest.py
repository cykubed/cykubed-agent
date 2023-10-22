import pytest
from httpx import Response
from loguru import logger
from redis import Redis
from redis.asyncio import Redis as AsyncRedis

from common.enums import PlatformEnum
from common.schemas import Project, NewTestRun
from settings import settings


@pytest.fixture()
def sync_redis():
    return Redis(host='localhost', db=1, decode_responses=True)


@pytest.fixture()
def redis(mocker, sync_redis, autouse=True):
    sync_redis.flushdb()
    aredis = AsyncRedis(host='localhost', db=1, decode_responses=True)
    mocker.patch('common.redisutils.get_cached_async_redis', return_value=aredis)
    logger.remove()
    return aredis


@pytest.fixture()
def mock_create_from_dict(mocker):
    return mocker.patch('k8utils.create_from_dict')


@pytest.fixture()
async def project() -> Project:
    # enable spot
    settings.PLATFORM = "GKE"
    settings.VOLUME_SNAPSHOT_CLASS = 'cykubed-snapshotclass'
    return Project(id=10,
                   organisation_id=5,
                   name='project',
                   repos='project',
                   default_branch='master',
                   agent_id=1,
                   platform=PlatformEnum.GITHUB,
                   build_cpu='4.0',
                   build_memory=6.0,
                   build_storage=10,
                   build_ephemeral_storage=4,
                   runner_cpu='2',
                   runner_memory=4.0,
                   runner_image='cykubed-runner:1234',
                   runner_ephemeral_storage=2,
                   url='git@github.org/dummy.git',
                   build_deadline=3600,
                   build_cmd='ng build --output=dist')


@pytest.fixture()
def k8_batch_api_mock(mocker):
    batch_api_mock = mocker.AsyncMock()
    mocker.patch('k8utils.get_batch_api', return_value=batch_api_mock)
    return batch_api_mock


@pytest.fixture()
def k8_core_api_mock(mocker):
    core_api_mock = mocker.AsyncMock()
    mocker.patch('k8utils.get_core_api', return_value=core_api_mock)
    return core_api_mock


@pytest.fixture()
def k8_custom_api_mock(mocker):
    api_mock = mocker.AsyncMock()
    mocker.patch('k8utils.get_custom_api', return_value=api_mock)
    return api_mock


@pytest.fixture()
def delete_pvc_mock(mocker, k8_core_api_mock):
    delete_pvc = mocker.AsyncMock()
    k8_core_api_mock.delete_namespaced_persistent_volume_claim = delete_pvc
    return delete_pvc


@pytest.fixture()
def create_custom_mock(mocker, k8_custom_api_mock):
    api_mock = mocker.AsyncMock()
    k8_custom_api_mock.create_namespaced_custom_object = api_mock
    return api_mock


@pytest.fixture()
def testrun(project: Project) -> NewTestRun:
    return NewTestRun(url='git@github.org/dummy.git',
                      id=20,
                      sha='deadbeef0101',
                      local_id=1,
                      project=project,
                      status='started',
                      branch='master',
                      spot_percentage=80)


@pytest.fixture()
def post_started_status(respx_mock):
    return respx_mock.post('https://api.cykubed.com/agent/testrun/20/status/started') \
            .mock(return_value=Response(200))


@pytest.fixture()
def post_building_status(respx_mock):
    return respx_mock.post('https://api.cykubed.com/agent/testrun/20/status/building') \
            .mock(return_value=Response(200))
