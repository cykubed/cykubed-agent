from starlette.testclient import TestClient

from common.schemas import NewTestRun, CompletedBuild
from main import app
from mongo import runs_coll, specfile_coll
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
    doc = await runs_coll().find_one({'id': 10})
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
    args = create_job.call_args_list[0].args[0]

    assert args.metadata.name == 'cykube-run-project-1'




