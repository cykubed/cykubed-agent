from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class SpecFile(BaseModel):
    file: str
    started: Optional[datetime] = None
    finished: Optional[datetime] = None

    class Config:
        orm_mode = True


class TestRun(BaseModel):
    id: int
    started: datetime
    finished: Optional[datetime] = None
    sha: str
    branch: str
    active: bool
    status: str
    files: List[SpecFile]

    commit_summary: str
    commit_link: str
    results_url: Optional[str]

    avatar: Optional[str]
    author: Optional[str]
    jira_ticket: Optional[str]

    class Config:
        orm_mode = True


class CodeFrame(BaseModel):
    line: int
    column: int
    file: str
    frame: str


class TestResultError(BaseModel):
    name: str
    message: str
    stack: str
    code_frame: CodeFrame
    screenshots: List[str]
    videos: List[str]


class TestResult(BaseModel):
    title: str
    failed: bool
    body: str
    num_attempts: int
    duration: int
    display_error: Optional[str]
    started_at: datetime
    error: Optional[TestResultError]


class SpecResult(BaseModel):
    file: str
    results: List[TestResult]


class Results(BaseModel):
    testrun: TestRun
    specs: List[SpecResult]
    failures: int = 0



