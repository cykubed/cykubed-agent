import json
import os.path

import yaml
from freezegun import freeze_time

import messages
from common.db import send_build_started_message, set_build_details
from common.enums import TestRunStatus
from common.schemas import NewTestRun
from ws import handle_message, poll_messages

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), 'fixtures')


def compare_rendered_template(create_from_yaml_mock, jobtype: str):
    yamlobjects = create_from_yaml_mock.call_args[1]['yaml_objects'][0]
    asyaml = yaml.dump(yamlobjects, indent=4, sort_keys=True)
    with open(os.path.join(FIXTURES_DIR, f'rendered_{jobtype}_template.yaml'), 'r') as f:
        expected = f.read()
        assert expected == asyaml


async def test_start_run(aredis, mocker, testrun: NewTestRun):
    """
    Check that the build job would be created, and simulate the start of that job
    by changing the status to 'building'
    :param mocker:
    :param testrun:
    :return:
    """
    deletejobs = mocker.patch('ws.jobs.delete_jobs_for_branch')
    create_from_yaml = mocker.patch('jobs.k8utils.create_from_yaml')
    trjson = testrun.json()
    await handle_message(dict(command='start', payload=trjson))
    # this will add an entry to the testrun collection
    saved_tr_json = await aredis.get(f'testrun:{testrun.id}')
    savedtr = NewTestRun.parse_raw(saved_tr_json)
    assert savedtr == testrun
    # and kick off the build job
    create_from_yaml.assert_called_once()
    deletejobs.assert_called_once_with(testrun.id, 'master')

    # mock out the actual Job to check the rendered template
    compare_rendered_template(create_from_yaml, 'build')


@freeze_time('2022-04-03 14:10:00Z')
async def test_build_messages(mocker, aredis, testrun: NewTestRun):
    create_from_yaml = mocker.patch('jobs.k8utils.create_from_yaml')

    await aredis.set(f'testrun:{testrun.id}', testrun.json())
    await send_build_started_message(testrun.id)
    await poll_messages(1)
    msg = json.loads(await messages.queue.get())
    messages.queue.task_done()
    assert msg == {"type": "build_started", "testrun_id": 20, "started": "2022-04-03T14:10:00+00:00"}
    # most messages are just forwarded on through the websocket, but we intercept the build completed
    # one to enable us to kick off the runner Job
    # The runner will have already set the build details: this is called by the runner
    testrun.sha = 'deadbeef0101'
    await set_build_details(testrun, ['cypress/e2e/spec1.ts', 'cypress/e2e/spec2.ts'])
    # this will result in 2 new messages
    specs = await aredis.smembers(f'testrun:{testrun.id}:specs')
    assert specs == {'cypress/e2e/spec1.ts', 'cypress/e2e/spec2.ts'}
    tr = NewTestRun.parse_raw(await aredis.get(f'testrun:{testrun.id}'))
    assert tr.status == TestRunStatus.running

    await poll_messages(2)

    create_from_yaml.assert_called_once()
    # mock out the actual Job to check the rendered template
    compare_rendered_template(create_from_yaml, 'runner')

    # two messages will be sent through the websocket
    msg1 = json.loads(await messages.queue.get())
    messages.queue.task_done()
    assert msg1 == {'type': 'build_completed', 'testrun_id': 20, 'sha': 'deadbeef0101',
                    'finished': '2022-04-03T14:10:00+00:00', 'specs': ['cypress/e2e/spec1.ts', 'cypress/e2e/spec2.ts']}
    msg2 = json.loads(await messages.queue.get())
    messages.queue.task_done()
    assert msg2 == {'type': 'status', 'testrun_id': 20, 'status': 'running'}

