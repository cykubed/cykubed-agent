import asyncio
import datetime
import os

from starlette.testclient import TestClient

from asyncmongo import runs_coll, specs_coll, new_run, set_build_details, spec_completed
from cache import get_app_distro_filename, cleanup
from common.enums import TestRunStatus, AgentEventType
from common.schemas import NewTestRun, CompletedBuild, SpecResult, TestResult, CompletedSpecFile, AgentSpecStarted, \
    AgentSpecCompleted
from common.settings import settings
from common.utils import utcnow
from ws import handle_message


async def test_start_run(mocker, testrun: NewTestRun):
    deletejobs = mocker.patch('ws.jobs.delete_jobs_for_branch')
    create_build_job = mocker.patch('jobs.create_job')
    await handle_message(dict(command='start', payload=testrun.json()))
    # this will add an entry to the testrun collection
    doc = await runs_coll().find_one({'id': 20})
    assert doc['url'] == 'git@github.org/dummy.git'
    # and create the build Job
    deletejobs.assert_called_once()
    create_build_job.assert_called_once()

    # update the status to building
    async def update_status():
        await runs_coll().find_one_and_update({'id': 20}, {'$set': {'status': 'building'}})

    add_msg = mocker.patch('main.messages.queue.add_agent_msg')
    await asyncio.gather(poll_testruns(1), update_status())

    add_msg.assert_called_once()


async def test_build_completed(mocker, testrun: NewTestRun):
    client = TestClient(app)
    delete_jobs = mocker.patch('ws.jobs.delete_jobs_for_branch')
    create_build_job = mocker.patch('ws.jobs.create_build_job')
    create_job = mocker.patch('jobs.create_job')
    mocker.patch('main.messages')
    await handle_message(dict(command='start', payload=testrun.json()))
    doc = await runs_coll().find_one({'id': testrun.id})
    assert doc.get('sha') is None

    create_build_job.assert_called_once()

    resp = client.post(f'/testrun/{testrun.id}/build-complete',
                       json=CompletedBuild(sha='deadbeef00101',
                                           specs=['cypress/e2e/fish/test1.spec.ts',
                                                  'cypress/e2e/fowl/test2.spec.ts'
                                                  ],
                                           cache_hash='abbcs643').dict())
    assert resp.status_code == 200

    all_files = set()
    for doc in await specs_coll().find({'trid': testrun.id}).to_list():
        all_files.add(doc['file'])
    assert all_files == {'cypress/e2e/fish/test1.spec.ts', 'cypress/e2e/fowl/test2.spec.ts'}

    delete_jobs.assert_called_once()
    create_job.assert_called_once()


async def test_get_specs(testrun: NewTestRun):
    """
    We should be able to fetch all the specs until we get a 204
    Store the optional pod name as it will be useful to pods
    :param testrun:
    :return:
    """
    client = TestClient(app)
    await new_run(testrun)
    await set_build_details(testrun.id, CompletedBuild(sha='deadbeef00101',
                                                       specs=['cypress/e2e/stuff/test1.spec.ts',
                                                              'cypress/e2e/stuff/test2.spec.ts'],
                                                       cache_hash='deadbeef0101'))
    resp = client.get('/testrun/20/next', params={'name': 'cykube-run-20'})
    assert resp.status_code == 200
    assert resp.text == 'cypress/e2e/stuff/test1.spec.ts'
    f = await specs_coll().find_one({'file': 'cypress/e2e/stuff/test1.spec.ts'})
    assert f['started'] is not None

    resp = client.get('/testrun/20/next', params={'name': 'cykube-run-20'})
    assert resp.status_code == 200
    assert resp.text == 'cypress/e2e/stuff/test2.spec.ts'

    resp = client.get('/testrun/20/next', params={'name': 'cykube-run-20'})
    assert resp.status_code == 204


async def test_spec_completed(mocker, testrun: NewTestRun):
    add_agent_msg = mocker.patch('main.messages.queue.add_agent_msg')
    client = TestClient(app)
    await new_run(testrun)
    # add a dummy dist
    dummydist = get_app_distro_filename({'sha':'deadbeef00101'})
    with open(dummydist, 'w') as f:
        f.write('dummy')

    await set_build_details(testrun.id, CompletedBuild(sha='deadbeef00101',
                                                       specs=['cypress/e2e/stuff/test1.spec.ts',
                                                              'cypress/e2e/stuff/test2.spec.ts'],
                                                       cache_hash='deadbeef0101'))
    dt = datetime.datetime(2023, 3, 2, 10, 16, 0, 0, tzinfo=datetime.timezone.utc)
    mocker.patch('main.utcnow', return_value=dt)
    resp = client.get('/testrun/20/next', params={'name': 'cykube-run-20'})
    assert resp.status_code == 200
    file = resp.text
    assert file == 'cypress/e2e/stuff/test1.spec.ts'
    # this will send a AgentSpecStarted msg
    add_agent_msg.assert_called_once_with(AgentSpecStarted(file=file,
                                                           testrun_id=20,
                                                           type=AgentEventType.spec_started,
                                                           started=dt,
                                                           pod_name='cykube-run-20'))
    add_agent_msg.reset_mock()

    dt2 = dt + datetime.timedelta(minutes=5)
    result = CompletedSpecFile(
        file=file,
        finished=dt2,
        result=SpecResult(tests=[TestResult(title="Title",
                                            context="Context",
                                            status=TestRunStatus.passed)]))

    # complete the spec - this will remove it
    resp = client.post('/testrun/20/spec-completed', content=result.json())
    assert resp.status_code == 200
    spec = await specs_coll().find_one({'trid': testrun.id, 'file': file})
    assert spec is None

    add_agent_msg.assert_called_once_with(AgentSpecCompleted(
        testrun_id=20,
        type=AgentEventType.spec_completed,
        spec=result))

    # complete the other one - this will set the status to passed
    result.file = 'cypress/e2e/stuff/test2.spec.ts'
    resp = client.post('/testrun/20/spec-completed', content=result.json())
    assert resp.status_code == 200
    spec = await specs_coll().find_one({'trid': testrun.id, 'file': result.file})
    assert spec is None
    tr = await runs_coll().find_one({'id': testrun.id})
    assert tr['status'] == 'passed'

    # the testrun dist will still be there - it will be cleaned up later
    assert os.path.exists(dummydist)

    resp = client.get('/testrun/20/next', params={'name': 'cykube-run-20'})
    assert resp.status_code == 204


async def test_cache_cleanup(mocker, testrun: NewTestRun):
    trdict = testrun.dict()
    trdict['started'] = utcnow() - datetime.timedelta(seconds=settings.APP_DISTRIBUTION_CACHE_TTL + 10)
    await runs_coll().insert_one(trdict)

    await set_build_details(testrun.id, CompletedBuild(sha='deadbeef0101',
                                                       cache_hash='abcdef',
                                                       specs=['fish1.ts']))
    await spec_completed(testrun.id,
                         CompletedSpecFile(file='fish1.ts',
                                           finished=utcnow(),
                                           result=SpecResult(tests=[TestResult(title="Title",
                                                                               context="Context",
                                                                               status=TestRunStatus.passed)])))

    tr = await runs_coll().find_one({'id': testrun.id})
    assert tr['sha'] == 'deadbeef0101'
    assert tr['status'] == 'passed'
    dummydist = get_app_distro_filename(tr)
    with open(dummydist, 'w') as f:
        f.write('dummy')

    await cleanup()

    # this will delete the distribution
    assert not os.path.exists(dummydist)

    # and the state in the database
    tr = await runs_coll().find_one({'id': testrun.id})
    assert tr is None
    specs = await specs_coll().find({'trid': testrun.id}).to_list()
    assert specs == []
