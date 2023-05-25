import json
import os.path

import yaml
from freezegun import freeze_time
from httpx import Response

from common.enums import AgentEventType
from common.schemas import NewTestRun, AgentEvent, AgentCloneCompletedEvent, TestRunErrorReport, AgentTestRunErrorEvent
from db import get_cached_item, add_cached_item, new_testrun, add_build_snapshot_cache_item
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
        yamlobjs = args[1]['yaml_objects'][0]
        ret.append((yamlobjs['kind'], yamlobjs['metadata']['name']))
    return ret


def compare_rendered_template_from_mock(create_from_yaml_mock, jobtype: str, index=0):
    yamlobjects = create_from_yaml_mock.call_args_list[index][1]['yaml_objects']
    compare_rendered_template(yamlobjects, jobtype)


async def test_start_run_cache_miss(redis, mocker, testrun: NewTestRun,
                                    respx_mock, mock_create_from_yaml):
    """
    """
    update_status = \
        respx_mock.post('https://api.cykubed.com/agent/testrun/20/status/building') \
            .mock(return_value=Response(200))

    def get_new_pvc_name(prefix: str) -> str:
        return f'{prefix}-pvc'

    mocker.patch('jobs.get_new_pvc_name', side_effect=get_new_pvc_name)

    delete_jobs = mocker.patch('jobs.delete_jobs_for_branch')
    await handle_start_run(testrun)

    # this will add an entry to the testrun collection
    saved_tr_json = redis.get(f'testrun:{testrun.id}')
    savedtr = NewTestRun.parse_raw(saved_tr_json)
    assert savedtr == testrun

    state = await get_build_state(testrun.id)
    assert state.rw_build_pvc == 'build-rw-pvc'
    assert state.trid == testrun.id

    delete_jobs.assert_called_once_with(testrun.id, 'master')
    # there will be two calls here: one to create the PVC, another to run the build Job
    assert mock_create_from_yaml.call_count == 2

    # mock out the actual Job to check the rendered template
    compare_rendered_template_from_mock(mock_create_from_yaml, 'build-rw-pvc', 0)
    compare_rendered_template_from_mock(mock_create_from_yaml, 'clone', 1)

    assert update_status.called == 1


async def test_start_run_cache_hit(redis, mocker, testrun: NewTestRun,
                                   k8_custom_api_mock,
                                   respx_mock, mock_create_from_yaml):
    """
    """
    update_status = \
        respx_mock.post('https://api.cykubed.com/agent/testrun/20/status/running') \
            .mock(return_value=Response(200))

    build_completed = \
        respx_mock.post('https://api.cykubed.com/agent/testrun/20/build-completed').mock(return_value=Response(200))

    def get_new_pvc_name(prefix: str) -> str:
        return f'{prefix}-pvc'

    mocker.patch('jobs.get_new_pvc_name', side_effect=get_new_pvc_name)

    await add_build_snapshot_cache_item('deadbeef0101', 'node-absd234weefw', ['spec1.ts'])

    delete_jobs = mocker.patch('jobs.delete_jobs_for_branch')

    await handle_start_run(testrun)

    delete_jobs.assert_called_once_with(testrun.id, 'master')

    # this will add an entry to the testrun collection
    saved_tr_json = redis.get(f'testrun:{testrun.id}')
    savedtr = NewTestRun.parse_raw(saved_tr_json)
    assert savedtr == testrun

    assert redis.smembers('testrun:20:specs') == {'spec1.ts'}
    assert int(redis.get('testrun:20:to-complete')) == 1

    assert mock_create_from_yaml.call_count == 3
    compare_rendered_template_from_mock(mock_create_from_yaml, 'build-ro-pvc-from-snapshot', 0)
    compare_rendered_template_from_mock(mock_create_from_yaml, 'node-ro-pvc-from-snapshot', 1)
    compare_rendered_template_from_mock(mock_create_from_yaml, 'runner', 2)

    assert build_completed.call_count == 1
    assert update_status.call_count == 1


async def test_clone_completed_cache_miss(redis, mocker, mock_create_from_yaml,
                                          respx_mock,
                                          k8_core_api_mock,
                                          testrun: NewTestRun):
    """
    Clone completed, and we have no cached node dist: we'll need to create a RW
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
    await new_testrun(testrun)
    state = TestRunBuildState(trid=testrun.id, rw_build_pvc='build-rw-pvc')
    await state.save()

    mocker.patch('jobs.get_new_pvc_name', side_effect=lambda prefix: f'{prefix}-pvc')

    # read_pvc = k8_core_api_mock.read_namespaced_persistent_volume_claim \
    #     = mocker.Mock(return_value=None)
    await handle_agent_message(websocket, AgentCloneCompletedEvent(cache_key='absd234weefw',
                                                                   specs=['test1.ts'],
                                                                   testrun_id=testrun.id).json())

    # it will create two object: the node (RW) PVC and the build job
    compare_rendered_template_from_mock(mock_create_from_yaml, 'node-rw-pvc', 0)
    compare_rendered_template_from_mock(mock_create_from_yaml, 'build-job-node-cache-miss', 1)

    state = await get_build_state(testrun.id)
    assert state.rw_node_pvc == 'node-rw-pvc'
    assert state.ro_node_pvc is None
    assert state.specs == ['test1.ts']
    assert state.node_snapshot_name == 'node-absd234weefw'

    assert redis.smembers('testrun:20:specs') == {'test1.ts'}
    assert int(redis.get('testrun:20:to-complete')) == 1

    assert get_cached_item('node-pvc-absd234weefw')


async def test_full_run(redis, mocker, mock_create_from_yaml,
                        respx_mock,
                        k8_core_api_mock,
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
    mocker.patch('jobs.get_new_pvc_name', side_effect=lambda prefix: f'{prefix}-pvc')
    k8_create_custom = k8_custom_api_mock.create_namespaced_custom_object

    await handle_start_run(testrun)

    delete_jobs.assert_called_once()

    assert building_update_status.call_count == 1

    # clone completed
    websocket = mocker.AsyncMock()
    await handle_agent_message(websocket, AgentCloneCompletedEvent(cache_key='absd234weefw',
                                                                   specs=['test1.ts'],
                                                                   testrun_id=testrun.id).json())

    assert redis.smembers('testrun:20:specs') == {'test1.ts'}
    assert int(redis.get('testrun:20:to-complete')) == 1

    # build completed
    msg = AgentEvent(type=AgentEventType.build_completed,
                     duration=10,
                     testrun_id=testrun.id)

    await handle_agent_message(websocket, msg.json())

    assert build_completed.call_count == 1
    assert running_update_status.call_count == 1

    # run completed
    delete_pvc_mock = k8_core_api_mock.delete_namespaced_persistent_volume_claim

    await handle_run_completed(testrun.id)

    assert run_completed.called

    # clean up
    assert delete_pvc_mock.call_count == 4

    # this will have created 2 PVCs, 2 snapshots and 2 Jobs
    kinds_and_names = get_kind_and_names(mock_create_from_yaml)
    assert [('PersistentVolumeClaim', 'build-rw-pvc'),
            ('Job', 'cykubed-clone-20-deadbeef0101'),
            ('PersistentVolumeClaim', 'node-rw-pvc'),
            ('Job', 'cykubed-build-project-20'),
            ('PersistentVolumeClaim', 'node-ro-pvc'),
            ('PersistentVolumeClaim', 'build-ro-pvc'),
            ('Job', 'cykubed-runner-project-20-0')] == kinds_and_names

    assert k8_create_custom.call_count == 2
    compare_rendered_template([k8_create_custom.call_args_list[0].kwargs['body']], 'node-snapshot')
    compare_rendered_template([k8_create_custom.call_args_list[1].kwargs['body']], 'build-snapshot')


async def test_clone_completed_cache_hit(redis, mocker, mock_create_from_yaml,
                                         respx_mock,
                                         k8_core_api_mock,
                                         k8_custom_api_mock,
                                         testrun: NewTestRun):
    """
    Clone completed, but we have a snapshot for a node distribution. Created a RO PVC from the snapshot and a RO
    PVC from the build PVC, before creating the build job.
    :param redis:
    :param mocker:
    :param mock_create_from_yaml:
    :param respx_mock:
    :param k8_core_api_mock:
    :param testrun:
    :return:
    """

    websocket = mocker.AsyncMock()
    await new_testrun(testrun)
    state = TestRunBuildState(trid=testrun.id, rw_build_pvc='build-rw-pvc')
    await state.save()
    await add_cached_item('node-absd234weefw')

    mocker.patch('jobs.get_new_pvc_name', side_effect=lambda prefix: f'{prefix}-pvc')

    # it will also check that the snapshot exists
    read_snapshot = k8_custom_api_mock.get_namespaced_custom_object = mocker.Mock(return_value=True)

    await handle_agent_message(websocket, AgentCloneCompletedEvent(cache_key='absd234weefw',
                                                                   specs=['test2.js'],
                                                                   testrun_id=testrun.id).json())

    read_snapshot.assert_called_once()
    assert read_snapshot.call_args.kwargs == {'group': 'snapshot.storage.k8s.io',
                                              'version': 'v1beta1',
                                              'namespace': 'cykubed',
                                              'plural': 'volumesnapshots',
                                              'name': 'node-absd234weefw'}

    k8_core_api_mock.assert_not_called()

    # it will create a RO PVC for the node cache and the build job
    compare_rendered_template_from_mock(mock_create_from_yaml, 'node-ro-pvc-from-snapshot', 0)
    compare_rendered_template_from_mock(mock_create_from_yaml, 'build-job-node-cache-hit', 1)

    state = await get_build_state(testrun.id)
    assert state.rw_node_pvc is None
    assert state.ro_node_pvc == 'node-ro-pvc'
    assert state.node_snapshot_name == 'node-absd234weefw'


@freeze_time('2022-04-03 14:10:00Z')
async def test_build_completed_cache_miss(redis, mock_create_from_yaml,
                                          respx_mock, mocker,
                                          k8_core_api_mock,
                                          k8_custom_api_mock,
                                          testrun: NewTestRun):
    """
    A build is completed without using a cached node distribution. We will need to create snapshot for the node dist
    and then create RO PVCs for both build and node before creating the runner job.
    :param redis:
    :param mock_create_from_yaml:
    :param respx_mock:
    :param mocker:
    :param k8_core_api_mock:
    :param k8_custom_api_mock:
    :param testrun:
    :return:
    """
    msg = AgentEvent(type=AgentEventType.build_completed,
                     duration=10,
                     testrun_id=testrun.id)
    mocker.patch('jobs.get_new_pvc_name', side_effect=lambda prefix: f'{prefix}-pvc')

    build_completed = \
        respx_mock.post('https://api.cykubed.com/agent/testrun/20/build-completed') \
            .mock(return_value=Response(200))

    update_status = \
        respx_mock.post('https://api.cykubed.com/agent/testrun/20/status/running') \
            .mock(return_value=Response(200))

    websocket = mocker.AsyncMock()

    await new_testrun(testrun)
    state = TestRunBuildState(trid=testrun.id, rw_build_pvc='build-rw-pvc',
                              rw_node_pvc='node-rw-pvc',
                              specs=['test1.ts'],
                              node_snapshot_name='node-absd234weefw')
    await state.save()

    await handle_agent_message(websocket, msg.json())

    state = await get_build_state(testrun.id)
    assert state.ro_node_pvc == 'node-ro-pvc'

    assert redis.get(f'cache:build-{testrun.sha}') is not None

    # not cached - so create a snapshot of the node dist
    k8_create_custom = k8_custom_api_mock.create_namespaced_custom_object
    assert k8_create_custom.call_count == 2

    compare_rendered_template([k8_create_custom.call_args_list[0].kwargs['body']], 'node-snapshot')
    compare_rendered_template([k8_create_custom.call_args_list[1].kwargs['body']], 'build-snapshot')

    assert mock_create_from_yaml.call_count == 3
    compare_rendered_template_from_mock(mock_create_from_yaml, 'node-ro-pvc-from-snapshot', 0)
    compare_rendered_template_from_mock(mock_create_from_yaml, 'build-ro-pvc-from-snapshot', 1)
    compare_rendered_template_from_mock(mock_create_from_yaml, 'runner', 2)

    assert build_completed.called == 1
    assert update_status.called == 1


@freeze_time('2022-04-03 14:10:00Z')
async def test_build_completed_cache_hit(redis, mock_create_from_yaml,
                                         respx_mock, mocker,
                                         k8_core_api_mock,
                                         k8_custom_api_mock,
                                         testrun: NewTestRun):
    """
    A build is completed using a cached node distribution. We will already have a RO node PVC, so we just need
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
    msg = AgentEvent(type=AgentEventType.build_completed, duration=15, testrun_id=testrun.id)
    mocker.patch('jobs.get_new_pvc_name', side_effect=lambda prefix: f'{prefix}-pvc')

    build_completed = \
        respx_mock.post('https://api.cykubed.com/agent/testrun/20/build-completed').mock(return_value=Response(200))
    websocket = mocker.AsyncMock()

    update_status = \
        respx_mock.post('https://api.cykubed.com/agent/testrun/20/status/running') \
            .mock(return_value=Response(200))

    await new_testrun(testrun)
    state = TestRunBuildState(trid=testrun.id,
                              rw_build_pvc='build-rw-pvc',
                              ro_node_pvc='node-ro-pvc',
                              specs=['test1.ts'])
    await state.save()

    await handle_agent_message(websocket, msg.json())

    state = await get_build_state(testrun.id)
    assert state.ro_build_pvc == 'build-ro-pvc'
    assert state.rw_build_pvc == 'build-rw-pvc'
    assert state.ro_node_pvc == 'node-ro-pvc'
    assert state.rw_node_pvc is None

    # it will still create a snapshot of the build PVC
    k8_create_custom = k8_custom_api_mock.create_namespaced_custom_object
    assert k8_create_custom.call_count == 1

    compare_rendered_template([k8_create_custom.call_args_list[0].kwargs['body']], 'build-snapshot')

    # we'll already have a RO node PVC
    assert mock_create_from_yaml.call_count == 2
    compare_rendered_template_from_mock(mock_create_from_yaml, 'build-ro-pvc-from-snapshot', 0)
    compare_rendered_template_from_mock(mock_create_from_yaml, 'runner', 1)

    assert build_completed.called == 1
    assert update_status.called == 1


async def test_run_completed(redis, k8_core_api_mock, respx_mock, testrun):
    await new_testrun(testrun)
    state = TestRunBuildState(trid=testrun.id,
                              rw_build_pvc='build-rw-pvc',
                              ro_build_pvc='build-ro-pvc',
                              rw_node_pvc='node-rw-pvc',
                              ro_node_pvc='node-ro-pvc',
                              specs=['test1.ts'],
                              node_snapshot_name='node-absd234weefw')
    await state.save()

    delete_pvc_mock = k8_core_api_mock.delete_namespaced_persistent_volume_claim
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

    assert delete_pvc_mock.call_count == 4
    pvcs = {x.args[0] for x in delete_pvc_mock.call_args_list}
    assert pvcs == {'build-rw-pvc', 'build-ro-pvc', 'node-rw-pvc', 'node-ro-pvc'}

    assert redis.get(f'testrun:state:20') is None
    assert redis.get(f'testrun:20') is None
    assert redis.get(f'testrun:20:specs') is None


async def test_run_error(redis, mocker, respx_mock, testrun):
    await new_testrun(testrun)
    report = TestRunErrorReport(testrun_id=testrun.id,
                                stage='runner',
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
