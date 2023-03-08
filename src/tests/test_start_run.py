import asyncio
import datetime

from bson import utc

from asyncmongo import runs_coll, specs_coll, messages_coll
from common.enums import AgentEventType, TestRunStatus
from common.schemas import NewTestRun, AgentStatusChanged, AgentCompletedBuildMessage, AgentSpecCompleted, TestResult, \
    SpecResult
from main import poll_messages
from ws import handle_message


async def test_start_run(mocker, testrun: NewTestRun):
    """
    Check that the build job would be created, and simulate the start of that job
    by changing the status to 'building'
    :param mocker:
    :param testrun:
    :return:
    """
    deletejobs = mocker.patch('ws.jobs.delete_jobs_for_branch')
    create_build_job = mocker.patch('jobs.create_job')
    await handle_message(dict(command='start', payload=testrun.json()))
    # this will add an entry to the testrun collection
    doc = await runs_coll().find_one({'id': 20})
    assert doc['url'] == 'git@github.org/dummy.git'
    # and create the build Job
    deletejobs.assert_called_once()
    create_build_job.assert_called_once()

    assert await messages_coll().count_documents({}) == 0

    # update the status to building
    msg = AgentStatusChanged(testrun_id=20,
                             type=AgentEventType.status,
                             status='building').json()

    async def update_status():
        await runs_coll().find_one_and_update({'id': 20}, {'$set': {'status': 'building'}})
        await messages_coll().insert_one({'msg': msg})

    add_msg = mocker.patch('main.messages.queue.add_agent_msg')
    await asyncio.gather(poll_messages(1), update_status())

    add_msg.assert_called_once_with(msg)
    assert await messages_coll().count_documents({}) == 0


async def test_build_completed(mocker, testrun: NewTestRun):
    # update the status to building
    msg = AgentCompletedBuildMessage(testrun_id=testrun.id,
                                     type=AgentEventType.build_completed,
                                     sha='deadbeef0101',
                                     specs=['cypress/e2e/fish/test1.spec.ts',
                                            'cypress/e2e/fowl/test2.spec.ts'],
                                     cache_hash='abcdef0101').json()

    await messages_coll().insert_one({'msg': msg})

    add_msg = mocker.patch('main.messages.queue.add_agent_msg')
    await poll_messages(1)

    add_msg.assert_called_once_with(msg)


async def test_spec_completed(mocker, testrun: NewTestRun):
    dt = datetime.datetime(2023, 3, 1, 10, 0, 0, tzinfo=utc)
    await runs_coll().insert_one(
        {'id': 20,
         'started': dt,
         'failures': 1,
         'sha': 'deadbeef0101',
         'status': 'running'})
    await specs_coll().insert_one({'trid': testrun.id, 'file': 'spec1.ts', 'started': dt, 'finished': dt})
    msg = AgentSpecCompleted(testrun_id=20,
                             type=AgentEventType.spec_completed,
                             finished=dt,
                             file='spec1.ts',
                             result=SpecResult(tests=[TestResult(title="Title",
                                                                 context="Context",
                                                                 status=TestRunStatus.failed)])).json()
    await messages_coll().insert_one({'msg': msg})

    add_msg = mocker.patch('main.messages.queue.add_agent_msg')
    await poll_messages(1)

    add_msg.assert_called_once_with(msg)
