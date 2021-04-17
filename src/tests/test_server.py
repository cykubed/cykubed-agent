from datetime import datetime

import pytest
from pytest_mock import MockerFixture
from sqlalchemy.orm import Session
from starlette.background import BackgroundTasks
from starlette.testclient import TestClient

from conftest import override_get_db
from main import app
from models import TestRun, SpecFile, get_db

app.dependency_overrides[get_db] = override_get_db
# defaults to in-memory sqlite
client = TestClient(app)


@pytest.fixture
def test_run_fixture(db: Session):
    tr = TestRun(started=datetime(2020, 6, 1, 10, 0, 0),
                 sha='12345',
                 branch='master',
                 active=True,
                 status='running')
    tr.files = [SpecFile(id=1, file='file1.ts', started=datetime(2020, 6, 1, 10, 1, 0),
                         finished=datetime(2020, 6, 1, 10, 1, 5)),
                SpecFile(id=2, file='file2.ts', started=datetime(2020, 6, 1, 10, 1, 0)),
                SpecFile(id=3, file='file3.ts')]
    db.add(tr)
    db.commit()
    return tr


def test_hc():
    response = client.get('/hc')
    assert response.status_code == 200


def test_get_testruns(db: Session, test_run_fixture):
    # get it back again to check
    saved_tr = db.query(TestRun).join(SpecFile).first()
    assert len(saved_tr.files) == 3

    resp = client.get('/api/testruns')
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data == [{'active': True,
                     'branch': 'master',
                     'files': [{'file': 'file1.ts',
                                'started': '2020-06-01T10:01:00',
                                'finished': '2020-06-01T10:01:05'
                                },
                               {'file': 'file2.ts',
                                'finished': None,
                                'started': '2020-06-01T10:01:00'},
                               {'file': 'file3.ts',
                                'finished': None,
                                'started': None}
                               ],
                     'finished': None,
                     'id': 1,
                     'sha': '12345',
                     'started': '2020-06-01T10:00:00',
                     'status': 'running'}]


def test_start_run(mocker: MockerFixture):
    response = client.post('/api/start', json=dict(repos='myrepos', sha='12345', branch='master'))
    assert response.status_code == 200
    # FIXME mock the Celery task
    # add_task.assert_called_once()


def test_get_next(test_run_fixture):
    resp = client.get('/testrun/12345/next')
    assert 200 == resp.status_code
    assert resp.json() == {'id': 3, 'spec': 'file3.ts'}
    # do it again - should be 204
    resp = client.get('/testrun/12345/next')
    assert 204 == resp.status_code


def test_complete(db: Session, test_run_fixture, mocker: MockerFixture):
    create_report = mocker.patch('main.create_report')
    notify_failed = mocker.patch('main.notify_failed')
    notify_fixed = mocker.patch('main.notify_fixed')
    create_report.return_value = 0, []
    # complete file 2
    resp = client.post('/testrun/2/completed')
    assert 200 == resp.status_code
    spec = db.query(SpecFile).get(2)
    assert spec.finished
    # should won't create the report
    create_report.assert_not_called()
    # get the final file
    resp = client.get('/testrun/12345/next')
    id = resp.json()['id']
    assert id == 3
    spec = db.query(SpecFile).get(3)
    assert spec.started
    resp = client.post(f'/testrun/3/completed')
    assert 200 == resp.status_code
    create_report.assert_called_once()
    notify_fixed.assert_not_called()
    notify_failed.assert_not_called()


def test_failed(db: Session, test_run_fixture, mocker: MockerFixture):
    db.query(SpecFile).filter_by(id=2).update({'finished': datetime.now()})
    db.commit()
    create_report = mocker.patch('main.create_report')
    notify_failed = mocker.patch('main.notify_failed')
    notify_fixed = mocker.patch('main.notify_fixed')
    create_report.return_value = 1, ['file1.ts']
    resp = client.post('/testrun/3/completed')
    assert 200 == resp.status_code
    create_report.assert_called_once()
    notify_failed.assert_called_once()


def test_fixed(db: Session, test_run_fixture: TestRun, mocker: MockerFixture):
    # create an older failed TestRun in the same branch
    newtr = test_run_fixture
    tr = TestRun(sha='newsha123', branch=newtr.branch, started=datetime(2020, 1, 1, 10, 0, 0),
                 finished=datetime(2020, 1, 1, 10, 4, 0),
                 status='failed')
    db.add(tr)
    db.query(SpecFile).filter_by(id=2).update({'finished': datetime.now()})
    db.commit()
    create_report = mocker.patch('main.create_report')
    notify_failed = mocker.patch('main.notify_failed')
    notify_fixed = mocker.patch('main.notify_fixed')
    create_report.return_value = 0, []
    resp = client.post('/testrun/3/completed')
    assert 200 == resp.status_code
    create_report.assert_called_once()
    notify_failed.assert_not_called()
    notify_fixed.assert_called_once()
