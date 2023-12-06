import pytest
from dateutil.relativedelta import relativedelta
from httpx import Response
from loguru import logger
from redis import Redis
from redis.asyncio import Redis as AsyncRedis

from common import schemas
from common.enums import PlatformEnum
from common.schemas import Project, NewTestRun, TestRunBuildState
from common.utils import utcnow
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
    settings.PLATFORM = "gke"
    settings.VOLUME_SNAPSHOT_CLASS = 'cykubed-snapshotclass'
    settings.READ_ONLY_MANY = True
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
                   docker_image=dict(image='cykubed-runner:1234', browser='chrome',
                                     node_major_version=16),
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
def k8_delete_job_mock(k8_batch_api_mock, mocker):
    delete_job_mock = mocker.AsyncMock()
    k8_batch_api_mock.delete_namespaced_job = delete_job_mock
    return delete_job_mock


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
def testrun_factory(project: Project):
    def create():
        return NewTestRun(url='git@github.org/dummy.git',
                          id=20,
                          sha='deadbeef0101',
                          local_id=1,
                          project=project,
                          status='started',
                          branch='master',
                          spot_percentage=80,
                          buildstate=TestRunBuildState(testrun_id=20))
    return create


@pytest.fixture()
def testrun(testrun_factory) -> NewTestRun:
    return testrun_factory()


@pytest.fixture()
def post_started_status(respx_mock):
    return respx_mock.post('https://api.cykubed.com/agent/testrun/20/status/started') \
            .mock(return_value=Response(200))


@pytest.fixture()
def post_building_status(respx_mock):
    return respx_mock.post('https://api.cykubed.com/agent/testrun/20/status/building') \
            .mock(return_value=Response(200))


@pytest.fixture()
def save_build_state_mock(respx_mock):
    return respx_mock.put('https://api.cykubed.com/agent/testrun/20/build-state') \
                             .mock(return_value=Response(200))


@pytest.fixture()
def save_cached_item_mock(respx_mock):
    return respx_mock.post('https://api.cykubed.com/agent/cached-item') \
                             .mock(return_value=Response(200))


@pytest.fixture()
def delete_cached_item_mock_factory(respx_mock):
    def mock(key):
        return respx_mock.delete(f'https://api.cykubed.com/agent/cached-item/{key}') \
                             .mock(return_value=Response(200))
    return mock


@pytest.fixture()
def build_cache_miss_mock(mocker, respx_mock):
    return respx_mock.get('https://api.cykubed.com/agent/cached-item/5-build-deadbeef0101') \
        .mock(return_value=Response(404))


@pytest.fixture()
def get_cache_key_mock(mocker):
    return mocker.patch('jobs.get_cache_key', return_value='absd234weefw')


@pytest.fixture()
def node_cache_miss_mock(respx_mock, get_cache_key_mock):
    return respx_mock.get('https://api.cykubed.com/agent/cached-item/5-node-absd234weefw') \
        .mock(return_value=Response(404))


@pytest.fixture()
def cached_node_item() -> schemas.CacheItem:
    return schemas.CacheItem(name='5-node-absd234weefw', organisation_id=5,
                             storage_size=10,
                             expires=utcnow() + relativedelta(seconds=settings.NODE_DISTRIBUTION_CACHE_TTL))


@pytest.fixture()
def node_cache_hit_mock(cached_node_item: schemas.CacheItem, respx_mock, get_cache_key_mock):
    return respx_mock.get('https://api.cykubed.com/agent/cached-item/5-node-absd234weefw') \
        .mock(return_value=Response(200, content=cached_node_item.json()))


@pytest.fixture()
def wait_for_snapshot_ready_mock(mocker):
    return mocker.patch('jobs.wait_for_snapshot_ready', return_value=True)
