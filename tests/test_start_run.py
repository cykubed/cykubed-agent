import datetime
import json
import os.path

import yaml
from freezegun import freeze_time
from httpx import Response
from pytz import utc

from common.enums import TestRunStatus, AgentEventType
from common.schemas import NewTestRun, AgentTestRun, AgentEvent, CacheItemType
from db import expired_cached_items_iter, get_cached_item, add_cached_item
from jobs import handle_clone_completed
from settings import settings
from ws import handle_start_run, handle_agent_message

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), 'fixtures')


def compare_rendered_template(create_from_yaml_mock, jobtype: str, index=0):
    yamlobjects = create_from_yaml_mock.call_args_list[index][1]['yaml_objects']
    asyaml = yaml.safe_dump_all(yamlobjects, indent=4, sort_keys=True)
    # print('\n'+asyaml)
    with open(os.path.join(FIXTURES_DIR, 'rendered-templates', f'{jobtype}.yaml'), 'r') as f:
        expected = f.read()
        assert expected == asyaml


async def test_start_run(redis, mocker, testrun: NewTestRun,
                         respx_mock, mock_create_from_yaml):
    """
    """
    update_status = \
        respx_mock.post('https://api.cykubed.com/agent/testrun/20/status/building')\
            .mock(return_value=Response(200))

    delete_jobs = mocker.patch('jobs.delete_jobs_for_branch')
    await handle_start_run(testrun)
    # this will add an entry to the testrun collection
    saved_tr_json = redis.get(f'testrun:{testrun.id}')
    savedtr = NewTestRun.parse_raw(saved_tr_json)
    assert savedtr == testrun

    delete_jobs.assert_called_once_with(testrun.id, 'master')
    # there will be two calls here: one to create the PVC, another to run the build Job
    assert mock_create_from_yaml.call_count == 2

    # mock out the actual Job to check the rendered template
    compare_rendered_template(mock_create_from_yaml, 'build-pvc', 0)
    compare_rendered_template(mock_create_from_yaml, 'clone', 1)

    assert get_cached_item('build-deadbeef0101')

    assert update_status.called == 1


async def test_clone_completed_cache_miss(redis, mocker, mock_create_from_yaml,
                                          respx_mock,
                                          k8_core_api_mock,
                                          testrun: NewTestRun):
    websocket = mocker.AsyncMock()
    specs = ['cypress/e2e/spec1.ts', 'cypress/e2e/spec2.ts']
    atr = AgentTestRun(specs=specs, cache_key='absd234weefw', **testrun.dict())
    redis.sadd(f'testrun:{testrun.id}:specs', *specs)
    testrun.status = TestRunStatus.running
    redis.set(f'testrun:{testrun.id}', atr.json())

    read_pvc = k8_core_api_mock.read_namespaced_persistent_volume_claim \
        = mocker.Mock(return_value=None)
    await handle_agent_message(websocket, AgentEvent(type=AgentEventType.clone_completed,
                                                     testrun_id=testrun.id).json())

    read_pvc.assert_called_once_with('node-pvc-absd234weefw', 'cykubed')
    # it will create two object: the node (RW) PVC and the build job
    compare_rendered_template(mock_create_from_yaml, 'node-rw-pvc', 0)
    compare_rendered_template(mock_create_from_yaml, 'build', 1)

    assert get_cached_item('node-pvc-absd234weefw')


@freeze_time('2022-04-03 14:10:00Z')
async def test_check_add_cached_item(redis):
    await add_cached_item('node-snap-absd234weefw', CacheItemType.snapshot)
    cachestr = redis.get(f'cache:node-snap-absd234weefw')

    assert json.loads(cachestr) == {'name': 'node-snap-absd234weefw',
                                    'ttl': 108000,
                                    'type': 'snapshot',
                                    'expires': '2022-04-04T20:10:00+00:00'
                                    }


@freeze_time('2022-04-03 14:10:00Z')
async def test_create_build_job_node_cache_hit(redis, mock_create_from_yaml, mocker,
                                               respx_mock, k8_core_api_mock,
                                               k8_custom_api_mock,
                                               testrun: NewTestRun):
    specs = ['cypress/e2e/spec1.ts']
    testrun.status = TestRunStatus.running
    atr = AgentTestRun(specs=specs, node_cache_hit=True, cache_key='absd234weefw', **testrun.dict())
    redis.set(f'testrun:{testrun.id}', atr.json())
    redis.sadd(f'testrun:{testrun.id}:specs', *specs)

    read_pvc = k8_core_api_mock.read_namespaced_persistent_volume_claim \
        = mocker.Mock(return_value=True)
    read_snapshot = k8_custom_api_mock.get_namespaced_custom_object = mocker.Mock(return_value=True)

    await add_cached_item('node-snap-absd234weefw', CacheItemType.snapshot)

    await handle_clone_completed(testrun.id)

    read_pvc.assert_not_called()
    read_snapshot.assert_called_once()
    compare_rendered_template(mock_create_from_yaml, 'node-ro-pvc-from-snapshot', 0)
    compare_rendered_template(mock_create_from_yaml, 'build-node-cache-hit', 1)


@freeze_time('2022-04-03 14:10:00Z')
async def test_build_completed(redis, mock_create_from_yaml,
                               respx_mock, mocker,
                               k8_core_api_mock,
                               testrun: NewTestRun):
    msg = AgentEvent(type=AgentEventType.build_completed,
                     duration=10,
                     testrun_id=testrun.id)
    build_completed = \
        respx_mock.post('https://api.cykubed.com/agent/testrun/20/build-completed')\
            .mock(return_value=Response(200))
    websocket = mocker.AsyncMock()
    specs = ['cypress/e2e/test/test1.spec.ts']
    atr = AgentTestRun(specs=specs, cache_key='absd234weefw', **testrun.dict())
    redis.set(f'testrun:{testrun.id}', atr.json())

    await handle_agent_message(websocket, msg.json())
    websocket.send.assert_called_once_with(msg.json())
    # not a cached node_modules: delete the RW (we will have taken a snapshot)
    k8_core_api_mock.delete_namespaced_persistent_volume_claim.assert_called_with('node-pvc-absd234weefw', 'cykubed')

    assert mock_create_from_yaml.call_count == 3
    compare_rendered_template(mock_create_from_yaml, 'node-snapshot', 0)
    compare_rendered_template(mock_create_from_yaml, 'node-ro-clone-pvc', 1)
    compare_rendered_template(mock_create_from_yaml, 'runner', 2)

    assert build_completed.called == 1


async def test_expired_cache_iterator(redis, mocker, testrun: NewTestRun):
    mocker.patch('db.utcnow', return_value=datetime.datetime(2022, 1, 28, 10, 0, 0, tzinfo=utc))
    await add_cached_item('key1', CacheItemType.snapshot)
    items = []
    mocker.patch('db.utcnow', return_value=datetime.datetime(2022, 4, 28, 10, 0, 0, tzinfo=utc))
    async for item in expired_cached_items_iter():
        items.append(item)
    assert len(items) == 1


async def test_node_cache_expiry_update(redis, mocker, testrun: NewTestRun):
    atr = AgentTestRun(specs=['s.ts'], cache_key='absd234weefw', **testrun.dict())
    mocker.patch('db.utcnow', return_value=datetime.datetime(2022, 1, 28, 10, 0, 0, tzinfo=utc))
    await add_cached_item('key1', CacheItemType.snapshot)
    now = datetime.datetime(2022, 4, 28, 10, 0, 0, tzinfo=utc)
    mocker.patch('db.utcnow', return_value=now)
    # fetch it
    item = await get_cached_item('key1', atr)
    assert item.expires == now + datetime.timedelta(seconds=settings.NODE_DISTRIBUTION_CACHE_TTL)




