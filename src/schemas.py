from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel

from models import PlatformEnum


class SpecFile(BaseModel):
    file: str
    started: Optional[datetime] = None
    finished: Optional[datetime] = None

    class Config:
        orm_mode = True


class Status(str, Enum):
    building = 'building'
    cancelled = 'cancelled'
    running = 'running'
    timeout = 'timeout'
    failed = 'failed'
    passed = 'passed'


class TestRun(BaseModel):
    id: int
    started: datetime
    finished: Optional[datetime] = None
    repos: str
    sha: str
    branch: str
    active: bool
    status: Status
    files: List[SpecFile]

    commit_summary: Optional[str]
    commit_link: Optional[str]
    pull_request_link: Optional[str]

    avatar: Optional[str]
    author_slack_id: Optional[str]
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
    duration: Optional[int]; display_error: Optional[str]
    started_at: Optional[datetime]
    error: Optional[TestResultError]


class SpecResult(BaseModel):
    file: str
    results: List[TestResult]


class Results(BaseModel):
    testrun: TestRun
    specs: List[SpecResult]
    total: int = 0
    skipped: int = 0
    passes: int = 0
    failures: int = 0


class OAuthDetailsModel(BaseModel):
    access_token: str
    refresh_token: str
    expiry: datetime
    url: Optional[str]

    class Config:
        orm_mode = True


class ProjectModel(BaseModel):
    platform: PlatformEnum
    name: str
    url: str

    class Config:
        orm_mode = True


class SettingsModel(BaseModel):
    bitbucket: Optional[OAuthDetailsModel]
    jira: Optional[OAuthDetailsModel]
    slack: Optional[OAuthDetailsModel]

