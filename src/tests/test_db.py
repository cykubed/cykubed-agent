from datetime import datetime

import pytest

import crud
from models import TestRun, SpecFile


@pytest.fixture(scope='function')
def params() -> crud.TestRunParams:
    return crud.TestRunParams(repos='pegasus', branch='qa', sha='deadbeef')


def test_create_testrun(db, params):
    tr = crud.create_testrun(db, params, ['file1.ts', 'file2.ts'])
    assert tr.branch == 'qa'
    assert ['file1.ts', 'file2.ts'] == [f.file for f in tr.files]


def test_get_next_spec_file(db, params):
    assert 0 == len(db.query(SpecFile).all())
    crud.create_testrun(db, params, ['file1.ts', 'file2.ts'])
    assert 2 == len(db.query(SpecFile).all())
    f = crud.get_next_spec_file(db, params.sha)
    assert f.file == 'file1.ts'
    assert f.started is not None
    f = crud.get_next_spec_file(db, params.sha)
    assert f.file == 'file2.ts'
    f = crud.get_next_spec_file(db, params.sha)
    assert f is None


def test_mark_running(db, params):
    crud.create_testrun(db, params, ['file1.ts', 'file2.ts'])
    crud.mark_as_running(db, params.sha)
    tr = db.query(TestRun).filter_by(sha=params.sha).one()
    assert tr.status == 'running'


def test_mark_completed(db, params):
    crud.create_testrun(db, params, ['file1.ts'])
    f = crud.get_next_spec_file(db, params.sha)
    crud.mark_completed(db, f.id)
    f = db.query(SpecFile).filter_by(id=f.id).one()
    assert f.finished is not None


def test_apply_timeouts(db, mocker, params):
    mocker.patch('crud.now', return_value=datetime(2020, 1, 1))
    crud.create_testrun(db, params,  ['file1.ts', 'file2.ts'])
    tr = db.query(TestRun).first()
    assert tr.active

    # this should be a NOP with the same date
    crud.apply_timeouts(db, 24*3600*1, 1)
    tr = db.query(TestRun).first()
    assert tr.active

    # now time out the spec
    f = crud.get_next_spec_file(db, params.sha)
    mocker.patch('crud.now', return_value=datetime(2020, 1, 2))

    crud.apply_timeouts(db, 24 * 3600 * 3, 1)
    f = db.query(SpecFile).filter_by(id=f.id).one()
    assert f.started is None

    # move time onwards - TestRun is timed out
    mocker.patch('crud.now', return_value=datetime(2020, 1, 3))
    crud.apply_timeouts(db, 1, 1)
    tr = db.query(TestRun).first()
    assert not tr.active



