from datetime import datetime

from cachetools import TTLCache
from fastapi_exceptions.exceptions import ValidationError

from common.enums import TestRunStatus
from common.schemas import TestRunDetail, NewTestRun, TestRunSpec

cache = TTLCache(maxsize=10000, ttl=3600*24)


def add_run(run: NewTestRun):
    cache[run.id] = run


def update_run(run: TestRunDetail):
    cache[run.id] = run


def update_status(trid: int, status: TestRunStatus):
    get_run(trid).status = status


def get_run(trid: int) -> TestRunDetail:
    return cache.get(trid)


def get_running_test(trid: int) -> TestRunDetail:
    tr = get_run(trid)
    if tr.status != 'running':
        raise ValidationError("Test run is not running")
    return tr


def get_next_spec(trid: int) -> TestRunSpec | None:
    tr = get_running_test(trid)
    if tr.files:
        for f in tr.files:
            if not f.started:
                f.started = datetime.now()
                return f
    return None


def mark_spec_completed(trid: int, specid: int):
    tr = get_running_test(trid)
    if tr.files:
        for f in tr.files:
            if f.id == specid:
                f.finished = datetime.now()
                return
