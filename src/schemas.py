from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel


class NewTestRun(BaseModel):
    id: int
    url: str
    sha: str
    branch: str
    parallelism: Optional[int]


class SpecFile(BaseModel):
    file: str
    started: Optional[datetime] = None
    finished: Optional[datetime] = None


class Status(str, Enum):
    building = 'building'
    cancelled = 'cancelled'
    running = 'running'
    timeout = 'timeout'
    failed = 'failed'
    passed = 'passed'


class TestRun(NewTestRun):
    started: datetime
    finished: Optional[datetime] = None
    active: bool
    status: Status
    files: List[SpecFile]
    remaining: List[SpecFile]

    class Config:
        orm_mode = True


