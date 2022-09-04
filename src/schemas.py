from common.schemas import *


class NewTestRun(BaseModel):
    id: int
    sha: str
    url: str
    branch: str
    build_cmd = 'ng build --output-path=dist'
    parallelism: Optional[int]


class SpecFile(BaseModel):
    file: str
    started: Optional[datetime] = None
    finished: Optional[datetime] = None

