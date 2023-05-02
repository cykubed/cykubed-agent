import json
import os.path

import yaml
from freezegun import freeze_time
from httpx import Response

from common.enums import TestRunStatus, AgentEventType
from common.schemas import NewTestRun, AgentTestRun, AgentEvent, CacheItemType
from db import get_cached_item, add_cached_item, get_node_snapshot_name
from jobs import handle_clone_completed, handle_run_completed
from ws import handle_start_run, handle_agent_message

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), 'fixtures')


def compare_rendered_template(yamlobjects, jobtype: str):
    asyaml = yaml.safe_dump_all(yamlobjects, indent=4, sort_keys=True)
    # print('\n'+asyaml)
    with open(os.path.join(FIXTURES_DIR, 'rendered-templates', f'{jobtype}.yaml'), 'r') as f:
        expected = f.read()
        assert expected == asyaml


def compare_rendered_template_from_mock(create_from_yaml_mock, jobtype: str, index=0):
    yamlobjects = create_from_yaml_mock.call_args_list[index][1]['yaml_objects']
    compare_rendered_template(yamlobjects, jobtype)


async def test_start_run(redis, mocker, testrun: NewTestRun,
                         respx_mock, mock_create_from_yaml):
    """
    """
    update_status = \
        respx_mock.post('https://api.cykubed.com/agent/testrun/20/status/building') \
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
    compare_rendered_template_from_mock(mock_create_from_yaml, 'build-pvc', 0)
    compare_rendered_template_from_mock(mock_create_from_yaml, 'clone', 1)

    assert get_cached_item('build-pvc-deadbeef0101')

    assert update_status.called == 1


async def test_clone_completed_cache_miss(redis, mocker, mock_create_from_yaml,
                                          respx_mock,
                                          k8_core_api_mock,
                                          testrun: NewTestRun):
    """
    Clone completed and we have no cached node dist: we'll need to create a RW
    PVC for the node dist
    :param redis:
    :param mocker:
    :param mock_create_from_yaml:
    :param respx_mock:
    :param k8_core_api_mock:
    :param testrun:
    :return:
    """
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
    compare_rendered_template_from_mock(mock_create_from_yaml, 'node-rw-pvc', 0)
    compare_rendered_template_from_mock(mock_create_from_yaml, 'build', 1)

    assert get_cached_item('node-pvc-absd234weefw')


async def test_clone_completed_cache_hit(redis, mocker, mock_create_from_yaml,
                                         respx_mock,
                                         k8_core_api_mock,
                                         k8_custom_api_mock,
                                         testrun: NewTestRun):
    """
    Clone completed but this time we have a snapshot for a node distribution,
    so go straight to creating a RO PVC from the snapshot
    :param redis:
    :param mocker:
    :param mock_create_from_yaml:
    :param respx_mock:
    :param k8_core_api_mock:
    :param testrun:
    :return:
    """

    websocket = mocker.AsyncMock()
    specs = ['cypress/e2e/spec1.ts']
    testrun.status = 'building'
    atr = AgentTestRun(specs=specs, node_cache_hit=True,
                       cache_key='absd234weefw', **testrun.dict())
    redis.set(f'testrun:{testrun.id}', atr.json())
    await add_cached_item(get_node_snapshot_name(atr), CacheItemType.snapshot)

    # it will also check that the snapshot exists
    read_snapshot = k8_custom_api_mock.get_namespaced_custom_object = mocker.Mock(return_value=True)

    await handle_agent_message(websocket, AgentEvent(type=AgentEventType.clone_completed,
                                                     testrun_id=testrun.id).json())

    read_snapshot.assert_called_once()
    assert read_snapshot.call_args.kwargs == {'group': 'snapshot.storage.k8s.io',
                                              'version': 'v1beta1',
                                              'namespace': 'cykubed',
                                              'plural': 'volumesnapshots',
                                              'name': 'node-snap-absd234weefw'}

    k8_core_api_mock.assert_not_called()

    # it will create a RO PVC for the node cache and the build job
    compare_rendered_template_from_mock(mock_create_from_yaml, 'node-ro-pvc-from-snapshot', 0)
    compare_rendered_template_from_mock(mock_create_from_yaml, 'build-node-cache-hit', 1)

    assert get_cached_item('node-ro-pvc-absd234weefw')


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
    compare_rendered_template_from_mock(mock_create_from_yaml, 'node-ro-pvc-from-snapshot', 0)
    compare_rendered_template_from_mock(mock_create_from_yaml, 'build-node-cache-hit', 1)


@freeze_time('2022-04-03 14:10:00Z')
async def test_build_completed(redis, mock_create_from_yaml,
                               respx_mock, mocker,
                               k8_core_api_mock,
                               k8_custom_api_mock,
                               testrun: NewTestRun):
    msg = AgentEvent(type=AgentEventType.build_completed,
                     duration=10,
                     testrun_id=testrun.id)

    build_completed = \
        respx_mock.post('https://api.cykubed.com/agent/testrun/20/build-completed') \
            .mock(return_value=Response(200))
    websocket = mocker.AsyncMock()
    specs = ['cypress/e2e/test/test1.spec.ts']
    atr = AgentTestRun(specs=specs, cache_key='absd234weefw', **testrun.dict())
    redis.set(f'testrun:{testrun.id}', atr.json())

    await handle_agent_message(websocket, msg.json())

    # not cached - so create a snapshot
    k8_create_custom = k8_custom_api_mock.create_namespaced_custom_object
    k8_create_custom.assert_called_once()
    compare_rendered_template([k8_create_custom.call_args.kwargs['body']], 'node-snapshot')

    assert mock_create_from_yaml.call_count == 2
    # compare_rendered_template_from_mock(mock_create_from_yaml, 'node-snapshot', 0)
    compare_rendered_template_from_mock(mock_create_from_yaml, 'node-ro-clone-pvc', 0)
    compare_rendered_template_from_mock(mock_create_from_yaml, 'runner', 1)

    assert build_completed.called == 1


async def test_run_completed(redis, k8_core_api_mock, testrun):
    specs = ['cypress/e2e/test/test1.spec.ts']
    atr = AgentTestRun(specs=specs, cache_key='absd234weefw', **testrun.dict())
    redis.set(f'testrun:{atr.id}', atr.json())

    delete_pvc_mock = k8_core_api_mock.delete_namespaced_persistent_volume_claim

    await handle_run_completed(testrun.id)

    assert delete_pvc_mock.call_count == 3
    assert delete_pvc_mock.call_args_list[0].args == ('node-pvc-absd234weefw', 'cykubed')
    assert delete_pvc_mock.call_args_list[1].args == ('node-ro-pvc-absd234weefw', 'cykubed')
    assert delete_pvc_mock.call_args_list[2].args == ('build-ro-pvc-deadbeef0101', 'cykubed')


