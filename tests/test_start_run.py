import json
import os.path

import yaml
from freezegun import freeze_time
from httpx import Response

from common.enums import AgentEventType
from common.schemas import NewTestRun, AgentEvent, TestRunErrorReport, AgentTestRunErrorEvent, AgentBuildCompletedEvent
from db import add_cached_item, new_testrun, add_build_snapshot_cache_item
from jobs import handle_run_completed, handle_testrun_error
from state import TestRunBuildState, get_build_state
from ws import handle_start_run, handle_agent_message

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), 'fixtures')


def compare_rendered_template(yamlobjects, jobtype: str):
    asyaml = yaml.safe_dump_all(yamlobjects, indent=4, sort_keys=True)
    # print('\n'+asyaml)
    with open(os.path.join(FIXTURES_DIR, 'rendered-templates', f'{jobtype}.yaml'), 'r') as f:
        expected = f.read()
        assert expected == asyaml


def get_kind_and_names(create_from_yaml_mock):
    ret = []
    for args in create_from_yaml_mock.call_args_list:
        yamlobjs = args[0][0]
        ret.append((yamlobjs['kind'], yamlobjs['metadata']['name']))
    return ret


def compare_rendered_template_from_mock(mock_create_from_dict, jobtype: str, index=0):
    yamlobjects = mock_create_from_dict.call_args_list[index].args[0]
    compare_rendered_template([yamlobjects], jobtype)


async def test_start_run_cache_miss(redis, mocker, testrun: NewTestRun,
                                    post_building_status,
                                    respx_mock, mock_create_from_dict):
    """
    New run with no node cache
    """
    get_cache_key = mocker.patch('jobs.get_cache_key', return_value='absd234weefw')

    delete_jobs = mocker.patch('jobs.delete_jobs_for_branch')
    await handle_start_run(testrun)

    get_cache_key.assert_called_once()

    # this will add an entry to the testrun collection
    saved_tr_json = redis.get(f'testrun:{testrun.id}')
    savedtr = NewTestRun.parse_raw(saved_tr_json)
    assert savedtr == testrun

    state = await get_build_state(testrun.id)
    assert state.rw_build_pvc == 'project-1-rw'
    assert state.trid == testrun.id

    delete_jobs.assert_called_once_with(testrun.id, 'master')
    # there will be two calls here: one to create the PVC, another to run the build Job
    assert mock_create_from_dict.call_count == 2

    # mock out the actual Job to check the rendered template
    compare_rendered_template_from_mock(mock_create_from_dict, 'build-rw-pvc', 0)
    compare_rendered_template_from_mock(mock_create_from_dict, 'build-job', 1)

    assert post_building_status.called == 1


async def test_start_run_node_cache_hit(redis, mocker, testrun: NewTestRun, post_building_status,
                                        k8_custom_api_mock,
                                        respx_mock, mock_create_from_dict):
    """
    New run with a node cache
    """
    await add_cached_item('node-absd234weefw', 10)

    get_cache_key = mocker.patch('jobs.get_cache_key', return_value='absd234weefw')

    delete_jobs = mocker.patch('jobs.delete_jobs_for_branch')
    async_get_snapshot = mocker.patch('jobs.async_get_snapshot', return_value=True)

    await handle_start_run(testrun)

    async_get_snapshot.assert_called_once()
    get_cache_key.assert_called_once()

    state = await get_build_state(testrun.id)
    assert state.node_snapshot_name == 'node-absd234weefw'

    # mock out the actual Job to check the rendered template
    compare_rendered_template_from_mock(mock_create_from_dict, 'build-rw-pvc-from-snapshot', 0)
    compare_rendered_template_from_mock(mock_create_from_dict, 'build-job', 1)

    assert post_building_status.called == 1


async def test_start_rerun(redis, mocker, testrun: NewTestRun,
                           k8_custom_api_mock,
                           respx_mock, mock_create_from_dict):
    """
    Called when we still have a RO build PVC available (i.e for a rerun)
    """
    update_status = \
        respx_mock.post('https://api.cykubed.com/agent/testrun/20/status/running') \
            .mock(return_value=Response(200))

    build_completed = \
        respx_mock.post('https://api.cykubed.com/agent/testrun/20/build-completed').mock(return_value=Response(200))

    mocker.patch('jobs.get_cache_key', return_value='absd234weefw')

    await add_cached_item('node-absd234weefw', 10)
    await add_build_snapshot_cache_item('deadbeef0101', 'node-absd234weefw', ['spec1.ts'], 1)

    delete_jobs = mocker.patch('jobs.delete_jobs_for_branch')

    await handle_start_run(testrun)

    delete_jobs.assert_called_once_with(testrun.id, 'master')

    # this will add an entry to the testrun collection
    saved_tr_json = redis.get(f'testrun:{testrun.id}')
    savedtr = NewTestRun.parse_raw(saved_tr_json)
    assert savedtr == testrun

    assert redis.smembers('testrun:20:specs') == {'spec1.ts'}
    assert int(redis.get('testrun:20:to-complete')) == 1

    assert mock_create_from_dict.call_count == 2
    compare_rendered_template_from_mock(mock_create_from_dict, 'build-ro-pvc-from-snapshot', 0)
    compare_rendered_template_from_mock(mock_create_from_dict, 'runner', 1)

    assert build_completed.call_count == 1
    assert update_status.call_count == 1


async def test_full_run(redis, mocker, mock_create_from_dict,
                        respx_mock,
                        k8_core_api_mock,
                        k8_batch_api_mock,
                        delete_pvc_mock,
                        k8_custom_api_mock,
                        testrun: NewTestRun):
    building_update_status = \
        respx_mock.post('https://api.cykubed.com/agent/testrun/20/status/building') \
            .mock(return_value=Response(200))
    running_update_status = \
        respx_mock.post('https://api.cykubed.com/agent/testrun/20/status/running') \
            .mock(return_value=Response(200))
    build_completed = \
        respx_mock.post('https://api.cykubed.com/agent/testrun/20/build-completed').mock(return_value=Response(200))
    run_completed = \
        respx_mock.post('https://api.cykubed.com/agent/testrun/20/run-completed') \
            .mock(return_value=Response(200))
    delete_jobs = mocker.patch('jobs.delete_jobs_for_branch')
    k8_create_custom = k8_custom_api_mock.create_namespaced_custom_object
    k8_delete_job = k8_batch_api_mock.delete_namespaced_job = mocker.AsyncMock()
    get_cache_key = mocker.patch('jobs.get_cache_key', return_value='absd234weefw')
    wait_for_pvc = mocker.patch('jobs.wait_for_pvc_ready', return_value=True)
    wait_for_snapshot = mocker.patch('jobs.wait_for_snapshot_ready', return_value=True)

    # start the run
    await handle_start_run(testrun)

    delete_jobs.assert_called_once()

    assert building_update_status.call_count == 1

    # build completed
    msg = AgentBuildCompletedEvent(type=AgentEventType.build_completed,
                                   specs=['test1.ts'],
                                   duration=10,
                                   testrun_id=testrun.id)

    websocket = mocker.AsyncMock()
    await handle_agent_message(websocket, msg.json())

    wait_for_pvc.assert_called_once_with('project-1-ro')

    assert build_completed.call_count == 1
    assert running_update_status.call_count == 1

    assert redis.smembers('testrun:20:specs') == {'test1.ts'}
    assert int(redis.get('testrun:20:to-complete')) == 1

    # the cache is preprared
    await handle_agent_message(websocket, AgentEvent(type=AgentEventType.cache_prepared,
                                                     testrun_id=testrun.id).json())

    wait_for_snapshot.assert_called_once()

    # run completed
    await handle_run_completed(testrun.id)

    assert run_completed.called

    # clean up
    assert delete_pvc_mock.call_count == 2

    # this will have created 2 PVCs, 2 snapshots and 2 Jobs
    kinds_and_names = get_kind_and_names(mock_create_from_dict)
    assert {('PersistentVolumeClaim', 'project-1-rw'),
            ('PersistentVolumeClaim', 'project-1-ro'),
            ('Job', 'builder-project-1'),
            ('Job', 'runner-project-1-0'),
            ('Job', 'cache-project-1')
            } == set(kinds_and_names)

    assert k8_create_custom.call_count == 2
    compare_rendered_template([k8_create_custom.call_args_list[0].kwargs['body']], 'build-snapshot')
    compare_rendered_template([k8_create_custom.call_args_list[1].kwargs['body']], 'node-snapshot')

    assert k8_delete_job.call_count == 2
    assert k8_delete_job.call_args_list[0].args == ('builder-project-1', 'cykubed')
    assert k8_delete_job.call_args_list[1].args == ('runner-project-1-0', 'cykubed')


@freeze_time('2022-04-03 14:10:00Z')
async def test_build_completed_node_cache_used(redis, mock_create_from_dict,
                                          respx_mock, mocker,
                                          k8_core_api_mock,
                                          create_custom_mock,
                                          testrun: NewTestRun):
    """
    A build is completed using a cached node distribution
    """
    msg = AgentBuildCompletedEvent(
                     testrun_id=testrun.id, specs=['test1.ts'])

    build_completed = \
        respx_mock.post('https://api.cykubed.com/agent/testrun/20/build-completed') \
            .mock(return_value=Response(200))

    update_status = \
        respx_mock.post('https://api.cykubed.com/agent/testrun/20/status/running') \
            .mock(return_value=Response(200))

    websocket = mocker.AsyncMock()

    await new_testrun(testrun)
    state = TestRunBuildState(trid=testrun.id,
                              rw_build_pvc='project-1-rw',
                              build_storage=10,
                              node_snapshot_name='node-absd234weefw',
                              cache_key='absd234weefw',
                              specs=['test1.ts'])
    await state.save()

    await handle_agent_message(websocket, msg.json())

    state = await get_build_state(testrun.id)
    assert state.ro_build_pvc == 'project-1-ro'

    assert redis.get(f'cache:build-{testrun.sha}') is not None

    # not cached - so create a snapshot of the node dist
    assert create_custom_mock.call_count == 1

    compare_rendered_template([create_custom_mock.call_args_list[0].kwargs['body']], 'build-snapshot')

    assert mock_create_from_dict.call_count == 2
    compare_rendered_template_from_mock(mock_create_from_dict, 'build-ro-pvc-from-snapshot', 0)
    compare_rendered_template_from_mock(mock_create_from_dict, 'runner', 1)

    assert build_completed.called == 1
    assert update_status.called == 1


@freeze_time('2022-04-03 14:10:00Z')
async def test_build_completed_no_node_cache(redis, mock_create_from_dict,
                                         respx_mock, mocker,
                                         k8_core_api_mock,
                                         create_custom_mock,
                                         testrun: NewTestRun):
    """
    A build is completed without using a cached node distribution. We will already have a RO node PVC, so we just need
    to create a RO PVCs for the build and the create the runner job.
    :param redis:
    :param mock_create_from_yaml:
    :param respx_mock:
    :param mocker:
    :param k8_core_api_mock:
    :param k8_custom_api_mock:
    :param testrun:
    :return:
    """
    msg = AgentBuildCompletedEvent(
                     testrun_id=testrun.id, specs=['test1.ts'])
    websocket = mocker.AsyncMock()

    respx_mock.post('https://api.cykubed.com/agent/testrun/20/build-completed') \
        .mock(return_value=Response(200))

    respx_mock.post('https://api.cykubed.com/agent/testrun/20/status/running') \
            .mock(return_value=Response(200))

    await new_testrun(testrun)
    # no node snapshot used
    state = TestRunBuildState(trid=testrun.id,
                              rw_build_pvc='project-1-rw',
                              build_storage=10,
                              cache_key='absd234weefw',
                              specs=['test1.ts'])
    await state.save()

    wait_for_pvc = mocker.patch('jobs.wait_for_pvc_ready', return_value=True)
    await handle_agent_message(websocket, msg.json())

    # not cached - wait for PVC to be ready then kick off the prepare job
    wait_for_pvc.assert_called_once()
    wait_for_pvc.assert_called_once_with('project-1-ro')
    assert mock_create_from_dict.call_count == 3
    compare_rendered_template_from_mock(mock_create_from_dict, 'build-ro-pvc-from-snapshot', 0)
    compare_rendered_template_from_mock(mock_create_from_dict, 'runner', 1)
    compare_rendered_template_from_mock(mock_create_from_dict, 'prepare-cache-job', 2)


async def test_prepare_cache_completed(mocker, redis, testrun,
                                       delete_pvc_mock,
                                       create_custom_mock):
    """
    Create a snapshot of the prepared build PVC
    """
    await new_testrun(testrun)
    # no node snapshot used
    state = TestRunBuildState(trid=testrun.id,
                              ro_build_pvc='project-1-ro',
                              rw_build_pvc='project-1-rw',
                              build_storage=10,
                              cache_key='absd234weefw',
                              specs=['test1.ts'])
    await state.save()
    websocket = mocker.AsyncMock()
    wait_for_snapshot = mocker.patch('jobs.wait_for_snapshot_ready', return_value=True)

    await handle_agent_message(websocket, AgentEvent(type=AgentEventType.cache_prepared,
                                                     testrun_id=testrun.id).json())
    assert create_custom_mock.call_count == 1
    compare_rendered_template([create_custom_mock.call_args_list[0].kwargs['body']], 'node-snapshot')
    # the build PVC will be deleted
    delete_pvc_mock.assert_called_once()
    wait_for_snapshot.assert_called_once()


async def test_run_completed(mocker, redis, k8_core_api_mock, k8_batch_api_mock, respx_mock, testrun):
    await new_testrun(testrun)
    state = TestRunBuildState(trid=testrun.id,
                              build_storage=10,
                              build_job='build-job',
                              run_job='run-job',
                              rw_build_pvc='project-1-rw',
                              ro_build_pvc='project-1-ro',
                              specs=['test1.ts'],
                              node_snapshot_name='node-absd234weefw')
    await state.save()

    delete_pvc_mock = k8_core_api_mock.delete_namespaced_persistent_volume_claim = mocker.AsyncMock()
    delete_job_mock = k8_batch_api_mock.delete_namespaced_job = mocker.AsyncMock()

    run_completed = \
        respx_mock.post('https://api.cykubed.com/agent/testrun/20/run-completed') \
            .mock(return_value=Response(200))

    redis.set('testrun:20:build:duration:normal', 420)
    redis.set('testrun:20:runner:duration:normal', 125)
    redis.set('testrun:20:runner:duration:spot', 250)

    await handle_run_completed(testrun.id)

    assert run_completed.called
    payload = json.loads(run_completed.calls.last.request.content)
    assert payload == {'testrun_id': 20,
                       'total_build_duration': 420, 'total_build_duration_spot': 0,
                       'total_runner_duration': 125, 'total_runner_duration_spot': 250}

    assert delete_pvc_mock.call_count == 2
    pvcs = {x.args[0] for x in delete_pvc_mock.call_args_list}
    assert pvcs == {'project-1-rw', 'project-1-ro'}

    assert delete_job_mock.call_count == 2
    jobs = {x.args[0] for x in delete_job_mock.call_args_list}
    assert jobs == {'build-job', 'run-job'}

    assert redis.get(f'testrun:state:20') is None
    assert redis.get(f'testrun:20') is None
    assert redis.get(f'testrun:20:specs') is None


async def test_run_error(redis, mocker, respx_mock, testrun):
    await new_testrun(testrun)
    report = TestRunErrorReport(stage='runner',
                                msg='Argh')
    handle_run_completed_mock = mocker.patch('jobs.handle_run_completed')
    run_error = \
        respx_mock.post('https://api.cykubed.com/agent/testrun/20/error') \
            .mock(return_value=Response(200))
    await handle_testrun_error(AgentTestRunErrorEvent(testrun_id=testrun.id,
                                                      report=report))
    handle_run_completed_mock.assert_called_once_with(testrun.id)

    assert run_error.called

    payload = json.loads(run_error.calls.last.request.content)
    assert payload == {'stage': 'runner', 'msg': 'Argh', 'error_code': None}
