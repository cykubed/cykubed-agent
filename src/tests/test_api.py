import os

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from common.enums import PlatformEnum
from common.schemas import NewTestRun, Project, TestRunDetail, SpecFile, TestRunSpec
from main import app
from testruns import add_run

client = TestClient(app)


@pytest.fixture()
def testrun():
    project = Project(id=10, name='dummyui', platform=PlatformEnum.GITHUB,
                      url='https://github.com/test/dummyui')
    newrun = NewTestRun(id=1, project=project, branch='master')
    add_run(newrun)
    return newrun


def test_cache():
    file = os.path.join(os.path.dirname(__file__), 'fixtures/dummy.txt')

    r = client.post(f'/upload', files={'file': ('dummy.txt',
                                                open(file, 'rb'), 'application/octet-stream')})
    assert r.status_code == 200
    fname = '/tmp/cykubecache/dummy.txt'
    assert os.path.exists(fname)
    with open(fname, 'r') as f:
        assert 'fish\n' == f.read()


def test_get_run(testrun):
    r = client.get(f'/testrun/{testrun.id}')
    assert r.status_code == 200
    tr = r.json()
    assert tr['status'] == 'started'
    assert tr['branch'] == 'master'


def test_set_specs(testrun, respx_mock):
    payload = dict(sha='deadbeef1010',
                             specs=['test/spec1.spec.ts',
                                    'test/spec2.spec.ts'])
    fulltr = TestRunDetail(id=1, project=testrun.project, branch='master',
                           sha=payload['sha'],
                           status='building',
                           files=[SpecFile(id=21, file='test/spec1.spec.ts'),
                                  SpecFile(id=22, file='test/spec2.spec.ts')])

    request = respx_mock.put("https://app.cykube.net/api/agent/testrun/1/specs",
                             json=payload).mock(return_value=Response(200, json=fulltr.dict()))

    r = client.put(f'/testrun/{testrun.id}/specs', json=payload)
    assert r.status_code == 200
    assert request.call_count == 1
    tr = TestRunDetail.parse_obj(r.json())
    assert len(tr.files) == 2
    # neither have started
    assert tr.files[0].started is None
    assert tr.files[1].started is None

    # update the status
    request = respx_mock.put("https://app.cykube.net/api/agent/testrun/1/status/running")
    r = client.put(f'/testrun/{testrun.id}/status/running')
    assert r.status_code == 200
    assert request.call_count == 1

    # fetch first one
    request = respx_mock.post("https://app.cykube.net/api/agent/testrun/1/spec-started/21")\
        .mock(return_value=Response(200, json=fulltr.files[0].dict()))

    r = client.get(f'/testrun/{testrun.id}/next-spec')
    assert r.status_code == 200
    assert request.call_count == 1
    spec = TestRunSpec.parse_obj(r.json())
    assert spec.file == 'test/spec1.spec.ts'

    # fetch the next one
    request = respx_mock.post("https://app.cykube.net/api/agent/testrun/1/spec-started/22") \
        .mock(return_value=Response(200, json=fulltr.files[1].dict()))

    r = client.get(f'/testrun/{testrun.id}/next-spec')
    assert r.status_code == 200
    assert request.call_count == 1
    spec = TestRunSpec.parse_obj(r.json())
    assert spec.file == 'test/spec2.spec.ts'

    # that's it
    r = client.get(f'/testrun/{testrun.id}/next-spec')
    assert r.status_code == 204

