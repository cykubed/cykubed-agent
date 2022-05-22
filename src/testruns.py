from copy import copy
from typing import Dict, List

from fastapi import HTTPException

from schemas import TestRun, SpecFile, NewTestRun, Status
from utils import now

testruns_by_id: Dict[id, TestRun] = {}


class HubException(Exception):
    pass


def get_next_spec(id: int) -> SpecFile:
    tr = testruns_by_id.get(id)
    if tr and len(tr.files) > 0:
        return tr.files.pop()


def add_run(testrun: NewTestRun) -> TestRun:
    tr = TestRun(**testrun.dict(), started=now(), active=True, status=Status.building)
    testruns_by_id[tr.id] = tr
    return tr


def get_run(id: int) -> TestRun:
    return testruns_by_id.get(id)


def set_specs(id: int, files: List[str]):
    specs = [SpecFile(file=s) for s in files]
    tr = testruns_by_id[id]
    if not tr:
        # no test run
        raise HTTPException(status_code=400, detail="No testrun in memory - was the hub restarted?")
    tr.files = specs
    tr.remaining = copy(specs)
    tr.status = Status.running
