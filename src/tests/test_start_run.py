import os

from starlette.testclient import TestClient

from common.enums import TestRunStatus
from common.schemas import NewTestRun, CompletedBuild, SpecResult, TestResult
from common.settings import settings
from main import app
from mongo import runs_coll, specfile_coll, new_run, set_build_details
from ws import handle_message


async def test_hc():
    client = TestClient(app)
    resp = client.get('/hc')
    assert resp.status_code == 200


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
    for doc in await specfile_coll().find({'trid': testrun.id}).to_list():
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
    f = await specfile_coll().find_one({'file': 'cypress/e2e/stuff/test1.spec.ts'})
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
    dummydist = os.path.join(settings.CYKUBE_CACHE_DIR, f'{testrun.id}.tar.lz4')
    with open(dummydist, 'w') as f:
        f.write('dummy')

    await set_build_details(testrun.id, CompletedBuild(sha='deadbeef00101',
                                                       specs=['cypress/e2e/stuff/test1.spec.ts',
                                                              'cypress/e2e/stuff/test2.spec.ts'],
                                                       cache_hash='deadbeef0101'))
    resp = client.get('/testrun/20/next', params={'name': 'cykube-run-20'})
    assert resp.status_code == 200
    file = resp.text
    assert file == 'cypress/e2e/stuff/test1.spec.ts'
    result = SpecResult(file=file,
                        tests=[TestResult(title="Title",
                                          context="Context",
                                          status=TestRunStatus.passed)])
    # complete the spec - this will remove it
    resp = client.post('/testrun/20/spec-completed', json=result.dict())
    assert resp.status_code == 200
    spec = await specfile_coll().find_one({'trid': testrun.id, 'file': file})
    assert spec is None
    add_agent_msg.assert_called_once()

    # complete the other one - this will remove the
    result.file = 'cypress/e2e/stuff/test2.spec.ts'
    resp = client.post('/testrun/20/spec-completed', json=result.dict())
    assert resp.status_code == 200
    spec = await specfile_coll().find_one({'trid': testrun.id, 'file': result.file})
    assert spec is None
    assert await runs_coll().find_one({'id': testrun.id}) is None
    # the testrun dist will have been removed
    assert not os.path.exists(dummydist)

    resp = client.get('/testrun/20/next', params={'name': 'cykube-run-20'})
    assert resp.status_code == 404
