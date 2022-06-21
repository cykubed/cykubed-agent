from copy import copy
from typing import Dict, List

from fastapi import HTTPException

from schemas import TestRun, SpecFile, NewTestRun, Status
from utils import now

testruns_by_id: Dict[int, TestRun] = {}


class HubException(Exception):
    pass


def add_run(testrun: NewTestRun) -> TestRun:
    # cancel any previous run on the same branch
    for tr in testruns_by_id.values():
        if tr.status == Status.running and tr.branch == testrun.branch:
            tr.status = Status.cancelled
    # and create a new one
    tr = TestRun(**testrun.dict(), started=now(), active=True, status=Status.building)
    testruns_by_id[tr.id] = tr
    return tr


def get_run(id: int) -> TestRun:
    return testruns_by_id.get(id)


def set_specs(id: int, files: List[str]) -> TestRun:
    specs = [SpecFile(file=s) for s in files]
    tr = get_run(id)
    if not tr:
        # no test run
        raise HTTPException(status_code=400, detail="No testrun in memory - was the hub restarted?")
    tr.files = specs
    tr.remaining = copy(specs)
    tr.status = Status.running
    return tr
