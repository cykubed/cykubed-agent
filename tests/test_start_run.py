import json
import os.path

import yaml
from freezegun import freeze_time
from httpx import Response

import common.schemas
from cache import add_cached_item, add_build_snapshot_cache_item
from common.enums import AgentEventType
from common.schemas import NewTestRun, AgentEvent, AgentBuildCompletedEvent, \
    Project, TestRunBuildState
from db import new_testrun
from jobs import create_runner_job, handle_delete_project
from settings import settings
from state import get_build_state
from state import save_build_state
from ws import handle_start_run, handle_websocket_message

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), 'fixtures')


def compare_rendered_template(yamlobjects, jobtype: str):
    asyaml = yaml.safe_dump_all(yamlobjects, indent=4, sort_keys=True)
    # print('\n' + asyaml)
    with open(os.path.join(FIXTURES_DIR, 'rendered-templates', f'{jobtype}.yaml'), 'r') as f:
        expected = f.read()
        assert asyaml == expected


def get_kind_and_names(create_from_yaml_mock):
    ret = []
    for args in create_from_yaml_mock.call_args_list:
        yamlobjs = args[0][0]
        ret.append((yamlobjs['kind'], yamlobjs['metadata']['name']))
    return ret


def compare_rendered_template_from_mock(mock_create_from_dict, jobtype: str, index=0):
    yamlobjects = mock_create_from_dict.call_args_list[index].args[0]
    compare_rendered_template([yamlobjects], jobtype)


async def test_start_run_cache_miss(mocker, testrun: NewTestRun,
                                    post_building_status,
                                    save_build_state_mock,
                                    post_started_status,
                                    node_cache_miss_mock,
                                    mock_create_from_dict):
    """
    New run with no node cache
    """
    await handle_start_run(testrun)

    assert post_started_status.called
    assert save_build_state_mock.called

    # there will be two calls here: one to create the PVC, another to run the build Job
    assert mock_create_from_dict.call_count == 2

    # mock out the actual Job to check the rendered template
    compare_rendered_template_from_mock(mock_create_from_dict, 'build-rw-pvc', 0)
    compare_rendered_template_from_mock(mock_create_from_dict, 'build-job-spot-gke', 1)

    assert post_building_status.called == 1


async def test_start_run_cache_miss_no_spot(mocker, testrun: NewTestRun,
                                            save_build_state_mock,
                                            post_building_status,
                                            post_started_status,
                                            node_cache_miss_mock,
                                            mock_create_from_dict):
    """
    New run with no node cache
    """
    testrun.spot_percentage = 0
    mocker.patch('jobs.get_cache_key', return_value='absd234weefw')
    mocker.patch('jobs.delete_jobs_for_branch')
    await handle_start_run(testrun)
    # mock out the actual Job to check the rendered template
    compare_rendered_template_from_mock(mock_create_from_dict, 'build-job-no-spot', 1)


async def test_start_run_cache_miss_spot_aks(mocker, testrun: NewTestRun,
                                             post_building_status,
                                             post_started_status,
                                             save_build_state_mock,
                                             node_cache_miss_mock,
                                             mock_create_from_dict):
    """
    New run with no node cache
    """
    settings.PLATFORM = 'aks'
    testrun.spot_percentage = 80
    mocker.patch('jobs.delete_jobs_for_branch')
    await handle_start_run(testrun)
    # mock out the actual Job to check the rendered template
    compare_rendered_template_from_mock(mock_create_from_dict, 'build-job-spot-aks', 1)


async def test_start_run_node_cache_hit(mocker, testrun: NewTestRun,
                                        post_building_status,
                                        post_started_status,
                                        k8_custom_api_mock,
                                        save_build_state_mock,
                                        save_cached_item_mock,
                                        node_cache_hit_mock,
                                        build_cache_miss_mock,
                                        mock_create_from_dict):
    """
    New run with a node cache
    """
    mocker.patch('jobs.delete_jobs_for_branch')
    async_get_snapshot = mocker.patch('jobs.async_get_snapshot', return_value=True)

    await handle_start_run(testrun)

    assert post_started_status.called
    assert node_cache_hit_mock.called
    async_get_snapshot.assert_called_once()

    # mock out the actual Job to check the rendered template
    compare_rendered_template_from_mock(mock_create_from_dict, 'build-rw-pvc-from-snapshot', 0)
    compare_rendered_template_from_mock(mock_create_from_dict, 'build-job-spot-gke', 1)

    assert post_building_status.called == 1


async def test_start_rerun(mocker, testrun: NewTestRun,
                           post_started_status,
                           save_build_state_mock,
                           save_cached_item_mock,
                           k8_custom_api_mock,
                           get_cache_key_mock,
                           respx_mock, mock_create_from_dict):
    """
    Called when we still have a RO build PVC available (i.e. for a rerun)
    """
    build_completed = \
        respx_mock.post('https://api.cykubed.com/agent/testrun/20/build-completed').mock(return_value=Response(200))

    item = await add_cached_item(testrun.project.organisation_id, '5-node-absd234weefw', 10)
    item = await add_build_snapshot_cache_item(testrun.project.organisation_id,
                                        'deadbeef0101', ['spec1.ts'], 1)
    testrun.buildstate.build_snapshot_name = '5-build-deadbeef0101'

    await handle_start_run(testrun)

    assert mock_create_from_dict.call_count == 1
    compare_rendered_template_from_mock(mock_create_from_dict, 'build-ro-pvc-from-snapshot', 0)

    assert build_completed.call_count == 1


async def test_create_full_spot_runner(testrun: NewTestRun,
                                       save_build_state_mock,
                                       mock_create_from_dict):
    testrun.spot_percentage = 100
    testrun.buildstate = TestRunBuildState(testrun_id=testrun.id,
                              run_job_index=1,
                              build_storage=10,
                              ro_build_pvc='5-project-1-ro',
                              specs=['spec1.ts'])
    await create_runner_job(testrun)
    compare_rendered_template_from_mock(mock_create_from_dict, 'runner-full-spot', 0)


async def test_create_runner_ephemeral_volumes(testrun: NewTestRun,
                                               save_build_state_mock,
                                               mock_create_from_dict):
    testrun.spot_percentage = 0
    settings.READ_ONLY_MANY = False
    testrun.buildstate = TestRunBuildState(testrun_id=testrun.id,
                           run_job_index=1,
                           build_storage=10,
                           build_snapshot_name='5-project-1-snap',
                           specs=['spec1.ts'])
    await create_runner_job(testrun)
    compare_rendered_template_from_mock(mock_create_from_dict, 'runner-ephemeral', 0)


async def test_create_runner_ephemeral_volumes_spot_aks(testrun: NewTestRun,
                                                        save_build_state_mock,
                                                        mock_create_from_dict):
    testrun.spot_percentage = 80
    settings.PLATFORM = 'aks'
    settings.READ_ONLY_MANY = False
    testrun.buildstate = TestRunBuildState(testrun_id=testrun.id,
                           run_job_index=1,
                           build_storage=10,
                           build_snapshot_name='5-project-1-snap',
                           specs=['spec1.ts'])
    await create_runner_job(testrun)
    compare_rendered_template_from_mock(mock_create_from_dict, 'runner-ephemeral-aks-spot', 0)


@freeze_time('2023-12-03 14:10:00Z')
async def test_full_run_gke(mocker, mock_create_from_dict,
                            respx_mock,
                            post_started_status,
                            wait_for_snapshot_ready_mock,
                            post_building_status,
                            save_build_state_mock,
                            k8_core_api_mock,
                            k8_batch_api_mock,
                            delete_pvc_mock,
                            k8_delete_job_mock,
                            k8_custom_api_mock,
                            get_cache_key_mock,
                            node_cache_miss_mock,
                            testrun_factory):
    """
    Full test run with node cache miss
    """

    testrun = testrun_factory()
    k8_create_custom = k8_custom_api_mock.create_namespaced_custom_object

    # start the run
    await handle_start_run(testrun)

    assert node_cache_miss_mock.called

    assert save_build_state_mock.call_count == 2

    # status is initial started, then building
    assert post_started_status.call_count == 1
    assert post_building_status.call_count == 1

    # this will have created a RW PVC and a build job
    kinds_and_names = set(get_kind_and_names(mock_create_from_dict))
    assert {('PersistentVolumeClaim', '5-project-1-rw'),
            ('Job', '5-builder-project-1')} == kinds_and_names

    # the builder job will called /agent/testrun/20/build-completed with the list of specs
    # the server will then call the agent with build-completed event

    # build completed
    testrun.total_files = 2
    state = testrun.buildstate
    state.specs = ['test1.ts', 'test2.ts']
    state.rw_build_pvc = '5-project-1-rw'
    state.build_job = '5-builder-project-1'

    respx_mock.reset()
    assert save_build_state_mock.call_count == 0

    await handle_websocket_message({'command': 'build_completed', 'payload': testrun.json()})

    # the agent takes a snapshot of the build and creates a RW PVC
    assert save_build_state_mock.call_count == 2
    payload = json.loads(save_build_state_mock.calls[0].request.content.decode())
    # first save state is when we kick off the snapshot
    assert payload == {"testrun_id": 20,
                       "specs": ["test1.ts", "test2.ts"],
                       "cache_key": "absd234weefw",
                       "build_snapshot_name": "5-build-deadbeef0101",
                       "node_snapshot_name": None,
                       "build_job": "5-builder-project-1",
                       "prepare_cache_job": None,
                       "preprovision_job": None,
                       "run_job": None,
                       "runner_deadline": None,
                       "completed": False,
                       "rw_build_pvc": "5-project-1-rw",
                       "ro_build_pvc": None,
                       "run_job_index": 0}

    payload = json.loads(save_build_state_mock.calls[1].request.content.decode())
    # the second is after we've created the snapshot, RO PVC and prepare node cache job
    assert payload == {"testrun_id": 20,
                       "specs": ["test1.ts", "test2.ts"],
                       "cache_key": "absd234weefw",
                       "build_snapshot_name": "5-build-deadbeef0101",
                       "node_snapshot_name": None,
                       "build_job": "5-builder-project-1",
                       "prepare_cache_job": "5-cache-project-1",
                       "preprovision_job": None,
                       "run_job": "5-runner-project-1-0",
                       "runner_deadline": "2023-12-03T15:10:00+00:00",
                       "completed": False,
                       "rw_build_pvc": "5-project-1-rw",
                       "ro_build_pvc": "5-project-1-ro",
                       "run_job_index": 0}

    testrun.buildstate = TestRunBuildState.parse_obj(payload)

    # this will create a RO PVC from the snapshot and kick off two jobs: one to prepare the node cache and
    # another to create the runner Job
    kinds_and_names = set(get_kind_and_names(mock_create_from_dict)) - kinds_and_names
    assert {('PersistentVolumeClaim', '5-project-1-ro'),
            ('Job', '5-runner-project-1-0'),
            ('Job', '5-cache-project-1')
            } == kinds_and_names

    assert wait_for_snapshot_ready_mock.called
    assert k8_create_custom.call_count == 1
    compare_rendered_template([k8_create_custom.call_args_list[0].kwargs['body']], 'build-snapshot')
    k8_create_custom.reset_mock()

    # the cache is preprared - we'll create a node snapshot
    await handle_websocket_message(dict(command='cache_prepared',
                                        payload=testrun.json()))

    assert k8_create_custom.call_count == 1
    compare_rendered_template([k8_create_custom.call_args_list[0].kwargs['body']], 'node-snapshot')

    # run completed
    await handle_websocket_message(dict(command='run_completed',
                                        payload=testrun.json()))

    # clean up
    delete_jobs = [x.args[0] for x in k8_delete_job_mock.call_args_list]
    assert delete_jobs == ['5-builder-project-1', '5-runner-project-1-0']

    delete_pvcs = [x.args[0] for x in delete_pvc_mock.call_args_list]
    assert delete_pvcs == ['5-project-1-rw', '5-project-1-ro']


@freeze_time('2023-12-03 14:10:00Z')
async def test_full_run_aks(redis, mocker, mock_create_from_dict,
                            respx_mock,
                            post_started_status,
                            post_building_status,
                            k8_core_api_mock,
                            k8_batch_api_mock,
                            wait_for_snapshot_ready_mock,
                            get_cache_key_mock,
                            node_cache_miss_mock,
                            save_build_state_mock,
                            delete_pvc_mock,
                            k8_custom_api_mock,
                            testrun: NewTestRun):
    settings.PLATFORM = 'aks'

    mocker.patch('jobs.wait_for_snapshot_ready', return_value=True)

    # start the run
    await handle_start_run(testrun)

    kinds_and_names = set(get_kind_and_names(mock_create_from_dict))
    assert {('PersistentVolumeClaim', '5-project-1-rw'),
            ('Job', '5-builder-project-1')} == kinds_and_names

    # as before, complete the build
    testrun.total_files = 1
    state = testrun.buildstate
    state.specs = ['test1.ts', 'test2.ts']
    state.rw_build_pvc = '5-project-1-rw'
    state.build_job = '5-builder-project-1'

    respx_mock.reset()
    await handle_websocket_message({'command': 'build_completed', 'payload': testrun.json()})

    # the agent takes a snapshot of the build and creates a RW PVC
    assert save_build_state_mock.call_count == 2
    payload = json.loads(save_build_state_mock.calls[1].request.content.decode())
    # First save state is the same as before. The second one will differ from GKE in
    # that we don't create a RO PVC
    assert payload == {"testrun_id": 20,
                       "specs": ["test1.ts", "test2.ts"],
                       "cache_key": "absd234weefw",
                       "build_snapshot_name": "5-build-deadbeef0101",
                       "node_snapshot_name": None,
                       "build_job": "5-builder-project-1",
                       "prepare_cache_job": "5-cache-project-1",
                       "preprovision_job": None,
                       "run_job": "5-runner-project-1-0",
                       "runner_deadline": "2023-12-03T15:10:00+00:00",
                       "completed": False,
                       "rw_build_pvc": "5-project-1-rw",
                       "ro_build_pvc": None,
                       "run_job_index": 0}

    testrun.buildstate = TestRunBuildState.parse_obj(payload)

    # in this mode we will create ephemeral volumes from the build snapshot
    kinds_and_names = set(get_kind_and_names(mock_create_from_dict)) - kinds_and_names
    assert {('Job', '5-runner-project-1-0'),
            ('Job', '5-cache-project-1')
            } == kinds_and_names

    assert wait_for_snapshot_ready_mock.called



@freeze_time('2022-04-03 14:10:00Z')
async def test_build_completed_node_cache_used(redis, mock_create_from_dict,
                                               respx_mock, mocker,
                                               post_building_status,
                                               k8_core_api_mock,
                                               create_custom_mock,
                                               testrun: NewTestRun):
    """
    A build is completed using a cached node distribution
    """
    mocker.patch('jobs.wait_for_snapshot_ready', return_value=True)

    msg = AgentBuildCompletedEvent(
        testrun_id=testrun.id, specs=['test1.ts'])

    build_completed = \
        respx_mock.post('https://api.cykubed.com/agent/testrun/20/build-completed') \
            .mock(return_value=Response(200))

    websocket = mocker.AsyncMock()

    await new_testrun(testrun)
    state = TestRunBuildState(testrun_idtestrun.id,
                              project_id=testrun.project.id,
                              rw_build_pvc='5-project-1-rw',
                              build_storage=10,
                              node_snapshot_name='node-absd234weefw',
                              cache_key='absd234weefw',
                              specs=['test1.ts'])
    await save_build_state(state)

    await handle_agent_message(websocket, msg.json())

    state = await get_build_state(testrun.id)
    assert state.ro_build_pvc == '5-project-1-ro'

    assert redis.get(f'cache:build-{testrun.sha}') is not None

    # not cached - so create a snapshot of the node dist
    assert create_custom_mock.call_count == 1

    compare_rendered_template([create_custom_mock.call_args_list[0].kwargs['body']], 'build-snapshot')

    assert mock_create_from_dict.call_count == 2
    compare_rendered_template_from_mock(mock_create_from_dict, 'build-ro-pvc-from-snapshot', 0)
    compare_rendered_template_from_mock(mock_create_from_dict, 'runner', 1)

    assert build_completed.called == 1


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
    mocker.patch('jobs.wait_for_snapshot_ready', return_value=True)
    msg = AgentBuildCompletedEvent(
        testrun_id=testrun.id, specs=['test1.ts'])
    websocket = mocker.AsyncMock()

    respx_mock.post('https://api.cykubed.com/agent/testrun/20/build-completed') \
        .mock(return_value=Response(200))

    respx_mock.post('https://api.cykubed.com/agent/testrun/20/status/running') \
        .mock(return_value=Response(200))

    await new_testrun(testrun)
    # no node snapshot used
    state = TestRunBuildState(testrun_idtestrun.id,
                              project_id=testrun.project.id,
                              rw_build_pvc='5-project-1-rw',
                              build_storage=10,
                              cache_key='absd234weefw',
                              specs=['test1.ts'])
    await save_build_state(state)

    await handle_agent_message(websocket, msg.json())

    # not cached - wait for PVC to be ready then kick off the prepare job
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
    st = TestRunBuildState(testrun_idtestrun.id,
                           project_id=testrun.project.id,
                           ro_build_pvc='5-project-1-ro',
                           rw_build_pvc='5-project-1-rw',
                           build_storage=10,
                           cache_key='absd234weefw',
                           specs=['test1.ts'])
    await save_build_state(st)
    websocket = mocker.AsyncMock()
    wait_for_snapshot = mocker.patch('jobs.wait_for_snapshot_ready', return_value=True)

    await handle_agent_message(websocket, AgentEvent(type=AgentEventType.cache_prepared,
                                                     testrun_id=testrun.id).json())
    assert create_custom_mock.call_count == 1
    compare_rendered_template([create_custom_mock.call_args_list[0].kwargs['body']], 'node-snapshot')
    # the build PVC will be deleted
    delete_pvc_mock.assert_called_once()
    wait_for_snapshot.assert_called_once()


async def test_delete_project(redis, mocker, project: Project):
    """
    Delete that delete_project deletes the relevant PVCs and jobs
    """
    st = common.schemas.TestRunBuildState(testrun_id100, project_id=project.id,
                                          ro_build_pvc='dummy-ro-1', build_job='build-1', run_job='run-1',
                                          build_storage=10,
                                          rw_build_pvc='dummy-rw-1')
    await save_build_state(st)

    st = common.schemas.TestRunBuildState(testrun_id101, project_id=project.id,
                                          ro_build_pvc='dummy-ro-2', build_job='build-2',
                                          build_storage=10,
                                          rw_build_pvc='dummy-rw-2')
    await save_build_state(st)

    st = common.schemas.TestRunBuildState(testrun_id102, project_id=6,
                                          ro_build_pvc='dummy-ro-3', build_job='build-3', run_job='run-3',
                                          build_storage=5,
                                          rw_build_pvc='dummy-rw-3')
    await save_build_state(st)

    keys = set(await redis.keys('testrun:state:*'))
    assert keys == {'testrun:state:102', 'testrun:state:101', 'testrun:state:100'}

    mock_clear_cache = mocker.patch('cache.clear_cache')
    mock_delete_job = mocker.patch('jobs.async_delete_job')
    mock_delete_pvc = mocker.patch('jobs.async_delete_pvc')

    await handle_delete_project(organisation_id=project.organisation_id,
                                project_id=project.id)

    assert mock_delete_pvc.call_count == 4
    assert mock_delete_job.call_count == 3
    assert mock_clear_cache.called_with_args(project.organisation_id)

    pvcs = {x.args[0] for x in mock_delete_pvc.call_args_list}
    assert pvcs == {'dummy-rw-2', 'dummy-ro-2', 'dummy-rw-1', 'dummy-ro-1'}

    jobs = {x.args[0] for x in mock_delete_job.call_args_list}
    assert jobs == {'build-2', 'build-1', 'run-1'}

    keys = await redis.keys('testrun:state:*')
    assert keys == ['testrun:state:102']
