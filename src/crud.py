from datetime import datetime, timedelta
from typing import Optional

from pydantic import BaseModel
from sqlalchemy import and_
from sqlalchemy import func
from sqlalchemy.orm import Session

from models import TestRun, SpecFile
from report import get_report_url
from schemas import Status
from utils import now


class TestRunParams(BaseModel):
    repos: str
    sha: str
    branch: str
    parallelism: Optional[int]
    spec_filter: Optional[str]


def count_test_runs(db: Session) -> int:
    return db.query(func.count(TestRun.id)).scalar()


def get_testrun(db: Session, id: int) -> TestRun:
    return db.query(TestRun).get(id)


def cancel_testrun(db: Session, tr: TestRun):
    tr.status = Status.cancelled
    tr.active = False
    db.add(tr)
    db.commit()


def create_testrun(db: Session, params: TestRunParams, specs=None, **info) -> TestRun:
    tr = TestRun(started=now(),
                 repos=params.repos,
                 sha=params.sha,
                 active=True,
                 branch=params.branch,
                 status=Status.building,
                 commit_summary=info.get('commit_summary'),
                 commit_link=info.get('commit_link'),
                 avatar=info.get('avatar'),
                 author=info.get('author'),
                 author_slack_id=info.get('author_slack_id'),
                 jira_ticket=info.get('jira_ticket'))
    db.add(tr)

    if specs:
        for spec in specs:
            db.add(SpecFile(testrun=tr, file=spec))
    db.commit()
    return tr


def update_test_run(db: Session, tr: TestRun, specs, **info):
    db.add(tr)
    for k, v in info.items():
        setattr(tr, k, v)
    for spec in specs:
        db.add(SpecFile(testrun=tr, file=spec))
    db.commit()


def cancel_previous_test_runs(db: Session, sha: str, branch: str):
    db.query(TestRun).filter_by(branch=branch).update({'status': 'cancelled', 'active': False})
    db.query(TestRun).filter_by(sha=sha).update({'status': 'cancelled', 'active': False})
    db.commit()


def get_last_specs(db: Session, sha: str):
    tr = db.query(TestRun).filter_by(sha=sha).join(SpecFile).order_by(TestRun.started.desc()).first()
    if tr:
        return [s.file for s in tr.files]


def mark_as_running(db: Session, tr: TestRun):
    # mark the spec as 'running'
    tr.status = Status.running
    db.add(tr)
    db.commit()


def get_test_run_status(db: Session, sha: str):
    tr = db.query(TestRun).filter_by(sha=sha, active=True).one_or_none()
    if tr:
        return tr.status.lower()


def get_test_runs(db: Session, page: int = 1, page_size: int = 50):
    return db.query(TestRun).join(SpecFile).order_by(TestRun.started.desc()).offset((page - 1) * page_size).limit(page_size).all()


def get_next_spec_file(db: Session, sha: str) -> SpecFile:
    spec = db.query(SpecFile).join(TestRun).with_for_update() \
        .filter(and_(TestRun.sha == sha, TestRun.active)) \
        .filter(SpecFile.started.is_(None)) \
        .first()
    if spec:
        spec.started = now()
        db.add(spec)
        db.commit()
    return spec


def mark_completed(db: Session, id: int) -> SpecFile:
    specfile = db.query(SpecFile).get(id)
    specfile.finished = now()
    db.add(specfile)
    db.commit()
    return specfile


def get_remaining(db: Session, testrun: TestRun) -> int:
    return db.query(SpecFile).filter_by(testrun=testrun, finished=None).count()


def mark_complete(db: Session, testrun: TestRun, total_fails: int):
    # mark run as finished and inactive
    testrun.finished = now()
    testrun.results_url = get_report_url(testrun.sha)
    testrun.active = False
    testrun.status = Status.failed if total_fails else Status.passed
    db.add(testrun)
    db.commit()


def get_last_run(db: Session, testrun: TestRun):
    return db.query(TestRun).filter_by(branch=testrun.branch) \
        .filter(TestRun.started < testrun.started) \
        .order_by(TestRun.started.desc()).one_or_none()


def apply_timeouts(db: Session, test_run_timeout: int, spec_file_timeout: int):
    dt = now() - timedelta(seconds=test_run_timeout)

    for run in db.query(TestRun).filter_by(active=True).filter(TestRun.started < dt):
        run.active = False
        run.status = Status.timeout
        run.finished = datetime.now()
        db.add(run)

    db.commit()

    dt = now() - timedelta(seconds=spec_file_timeout)

    for spec in db.query(SpecFile).join(TestRun).filter_by(finished=None) \
            .filter(TestRun.active == True, SpecFile.started < dt):
        spec.started = None
        db.add(spec)
    db.commit()

