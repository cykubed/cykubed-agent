import json
import os.path

import yaml
from freezegun import freeze_time
from httpx import Response

from common.enums import TestRunStatus, AgentEventType
from common.schemas import NewTestRun, AgentTestRun, AgentEvent, BuildCompletedAgentMessage
from common.utils import utcnow
from db import add_node_cache_item
from jobs import create_build_job
from ws import handle_start_run, handle_agent_message

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), 'fixtures')


def compare_rendered_template(create_from_yaml_mock, jobtype: str):
    yamlobjects = create_from_yaml_mock.call_args[1]['yaml_objects']
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
    # and kick off the build job
    mock_create_from_yaml.assert_called_once()

    # mock out the actual Job to check the rendered template
    compare_rendered_template(mock_create_from_yaml, 'clone')

    assert update_status.called == 1


async def test_clone_completed_cache_miss(redis, mocker, mock_create_from_yaml,
                                          respx_mock,
                                          testrun: NewTestRun):
    websocket = mocker.AsyncMock()
    specs = ['cypress/e2e/spec1.ts', 'cypress/e2e/spec2.ts']
    atr = AgentTestRun(specs=specs, cache_key='absd234weefw', **testrun.dict())
    redis.sadd(f'testrun:{testrun.id}:specs', *specs)
    testrun.status = TestRunStatus.running
    redis.set(f'testrun:{testrun.id}', atr.json())

    await handle_agent_message(websocket, AgentEvent(type=AgentEventType.clone_completed,
                                                     testrun_id=testrun.id).json())

    compare_rendered_template(mock_create_from_yaml, 'build')


@freeze_time('2022-04-03 14:10:00Z')
async def test_create_build_job_node_cache_hit(redis, mock_create_from_yaml,
                                               respx_mock,
                                               testrun: NewTestRun):
    specs = ['cypress/e2e/spec1.ts']
    testrun.status = TestRunStatus.running
    atr = AgentTestRun(specs=specs, node_cache_hit=True, cache_key='absd234weefw', **testrun.dict())
    redis.set(f'testrun:{testrun.id}', atr.json())
    redis.sadd(f'testrun:{testrun.id}:specs', *specs)
    await add_node_cache_item(atr)

    assert redis.sismember('builds', 'node-absd234weefw')
    assert json.loads(redis.get(f'build:node-absd234weefw')) == {'name': 'node-absd234weefw',
                                                            'ttl': 108000,
                                                            'expires': '2022-04-03T15:10:00+00:00'
                                                            }
    await create_build_job(testrun.id)
    compare_rendered_template(mock_create_from_yaml, 'build-node-cache-hit')


@freeze_time('2022-04-03 14:10:00Z')
async def test_build_completed(redis, mock_create_from_yaml,
                               respx_mock, mocker,
                               k8_core_api_mock,
                               testrun: NewTestRun):
    msg = BuildCompletedAgentMessage(type=AgentEventType.build_completed,
                                     testrun_id=testrun.id,
                                     finished=utcnow(),
                                     sha=testrun.sha, specs=['cypress/e2e/spec12.ts'])
    build_completed = \
        respx_mock.post('https://api.cykubed.com/agent/testrun/20/build-completed')\
            .mock(return_value=Response(200))
    websocket = mocker.AsyncMock()
    atr = AgentTestRun(specs=msg.specs, cache_key='absd234weefw', **testrun.dict())
    redis.set(f'testrun:{testrun.id}', atr.json())

    await handle_agent_message(websocket, msg.json())
    websocket.send.assert_called_once_with(msg.json())
    k8_core_api_mock.delete_namespaced_persistent_volume_claim.assert_called_with('build-deadbeef0101', 'cykubed')

    compare_rendered_template(mock_create_from_yaml, 'runner')

    assert build_completed.called == 1
