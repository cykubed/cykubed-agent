import os.path

import yaml

from common.schemas import NewTestRun
from ws import handle_message

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), 'fixtures')


def compare_rendered_template(create_from_yaml_mock, jobtype: str):
    yamlobjects = create_from_yaml_mock.call_args[1]['yaml_objects'][0]
    asyaml = yaml.dump(yamlobjects, indent=4, sort_keys=True)
    with open(os.path.join(FIXTURES_DIR, f'rendered_{jobtype}_template.yaml'), 'r') as f:
        expected = f.read()
        assert expected == asyaml


async def test_start_run(redis, mocker, mockapp, testrun: NewTestRun):
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
    await handle_message(mockapp, dict(command='start', payload=trjson))
    # this will add an entry to the testrun collection
    saved_tr_json = redis.get(f'testrun:{testrun.id}')
    savedtr = NewTestRun.parse_raw(saved_tr_json)
    assert savedtr == testrun
    # and kick off the build job
    create_from_yaml.assert_called_once()
    deletejobs.assert_called_once_with(testrun.id, 'master')

    # mock out the actual Job to check the rendered template
    compare_rendered_template(create_from_yaml, 'build')


# @freeze_time('2022-04-03 14:10:00Z')
# async def test_build_completed(respx_mock, mocker, redis, mockapp, testrun: NewTestRun):
#     create_from_yaml = mocker.patch('jobs.k8utils.create_from_yaml')
#     specs = ['cypress/e2e/spec1.ts', 'cypress/e2e/spec2.ts']
#     redis.sadd(f'testrun:{testrun.id}:specs', *specs)
#     testrun.status = TestRunStatus.running
#     testrun.sha = 'deadbeef0101'
#     redis.set(f'testrun:{testrun.id}', testrun.json())
#
#     msg = AgentCompletedBuildMessage(type=AgentEventType.build_completed,
#                                      testrun_id=testrun.id,
#                                      finished=utcnow(),
#                                      sha=testrun.sha, specs=specs)
#
#     route = respx_mock.post(f'http://localhost:5050/agent/testrun/20/build-completed')
#
#     async with respx_mock:
#         redis.rpush('messages', msg.json())
#         await poll_messages(mockapp, 1)
#         assert route.called
#         assert route.calls[0].request.method == 'POST'
#         assert json.loads(route.calls[0].request.content.decode()) == {"type": "build_completed", "testrun_id": 20,
#                                                                        "sha": "deadbeef0101",
#                                                                        "finished": "2022-04-03T14:10:00+00:00",
#                                                                        "specs": ["cypress/e2e/spec1.ts",
#                                                                                  "cypress/e2e/spec2.ts"]}
#
#     create_from_yaml.assert_called_once()
#     # mock out the actual Job to check the rendered template
#     compare_rendered_template(create_from_yaml, 'runner')

    # # two messages will be sent through the websocket
    # msg1 = json.loads(await messages.queue.get())
    # messages.queue.task_done()
    # assert msg1 == {'type': 'build_completed', 'testrun_id': 20, 'sha': 'deadbeef0101',
    #                 'finished': '2022-04-03T14:10:00+00:00', 'specs': ['cypress/e2e/spec1.ts', 'cypress/e2e/spec2.ts']}
    # msg2 = json.loads(await messages.queue.get())
    # messages.queue.task_done()
    # assert msg2 == {'type': 'status', 'testrun_id': 20, 'status': 'running'}

