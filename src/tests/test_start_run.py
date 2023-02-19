from starlette.testclient import TestClient

from common.schemas import NewTestRun, CompletedBuild
from main import app
from mongo import runs_coll
from ws import handle_message


async def test_hc():
    client = TestClient(app)
    resp = client.get('/hc')
    assert resp.status_code == 200


async def test_start_run(mocker, testrun: NewTestRun):
    deletejobs = mocker.patch('ws.jobs.delete_jobs_for_branch')
    create_build_job = mocker.patch('jobs.create_build_job')
    await handle_message(dict(command='start', payload=testrun.json()))
    # this will add an entry to the testrun collection
    doc = await runs_coll().find_one({'id': 10})
    assert doc['url'] == 'git@github.org/dummy.git'
    # and create the build Job
    deletejobs.assert_called_once()
    create_build_job.assert_called_with(testrun)


async def test_build_completed(mocker, testrun: NewTestRun):
    client = TestClient(app)
    mocker.patch('ws.jobs')
    await handle_message(dict(command='start', payload=testrun.json()))
    resp = client.post(f'/testrun/{testrun.id}/build-complete',
                       json=CompletedBuild(sha='deadbeef00101',
                                           specs=['cypress/e2e/fish/test1.spec.ts',
                                                  'cypress/e2e/fowl/test2.spec.ts'
                                                  ],
                                           cache_hash='abbcs643').dict())
    print(resp.json())
    assert resp.status_code == 200



