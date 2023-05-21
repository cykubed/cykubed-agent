from typing import Optional

from loguru import logger
from pydantic import BaseModel

from app import app
from common import schemas
from common.exceptions import BuildFailedException
from common.redisutils import async_redis
from common.schemas import AgentBuildCompleted


class TestRunBuildState(BaseModel):
    trid: int
    specs: list[str] = []
    parallelism: Optional[int]
    node_snapshot_name: Optional[str]
    jobs: list[str] = []
    rw_build_pvc: str
    rw_node_pvc: Optional[str]
    ro_build_pvc: Optional[str]
    ro_node_pvc: Optional[str]

    async def get_duration(self, cmd: str, spot: bool):
        spot_or_normal = 'spot' if spot else 'normal'
        val = await async_redis().get(f'testrun:{self.trid}:{cmd}:duration:{spot_or_normal}')
        return int(val) if val is not None else 0

    async def save(self):
        await async_redis().set(f'testrun:{self.trid}:state', self.json())

    async def notify_build_completed(self):
        resp = await app.httpclient.post(f'/agent/testrun/{self.trid}/build-completed',
                                         content=AgentBuildCompleted(specs=self.specs).json())
        if resp.status_code != 200:
            logger.error(f'Failed to update server that build was completed:'
                         f' {resp.status_code}: {resp.text}')

    async def notify_run_completed(self):
        payload = schemas.TestRunCompleted(
            testrun_id=self.trid,
            total_build_duration=await self.get_duration('build', False),
            total_build_duration_spot=await self.get_duration('build', True),
            total_runner_duration=await self.get_duration('runner', False),
            total_runner_duration_spot=await self.get_duration('runner', True))
        resp = await app.httpclient.post(f'/agent/testrun/{self.trid}/run-completed',
                                         json=payload.dict())
        if resp.status_code == 200:
            await self.delete_redis_state()
        else:
            logger.error(f'Failed to update testrun duration for testrun {self.trid}')

    async def delete_redis_state(self):
        r = async_redis()
        await r.delete(f'testrun:{self.trid}:state')
        await r.delete(f'testrun:{self.trid}:specs')
        await r.delete(f'testrun:{self.trid}')
        await r.srem('testruns', str(self.trid))


async def get_build_state(trid: int, check=False) -> TestRunBuildState:
    st = await async_redis().get(f'testrun:{trid}:state')
    if st:
        return TestRunBuildState.parse_raw(st)
    if check:
        raise BuildFailedException("Missing state")
