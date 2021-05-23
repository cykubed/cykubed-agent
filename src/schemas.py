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


