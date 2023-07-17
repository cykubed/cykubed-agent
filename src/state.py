import datetime
import json
from typing import Optional

from kubernetes_asyncio.client import V1PodStatus, V1ObjectMeta, V1Pod
from loguru import logger
from pydantic import BaseModel

from app import app
from common import schemas
from common.exceptions import BuildFailedException
from common.redisutils import async_redis
from common.schemas import AgentBuildCompleted
from common.utils import utcnow


class TestRunBuildState(BaseModel):
    trid: int
    specs: list[str] = []
    parallelism: Optional[int]
    build_storage: int
    cache_key: str = None
    node_snapshot_name: str = None
    build_job: str = None
    prepare_cache_job: str = None
    run_job: str = None
    runner_deadline: datetime.datetime = None
    run_job_index = 0
    rw_build_pvc: Optional[str]
    ro_build_pvc: Optional[str]

    async def get_duration(self, cmd: str, spot: bool):
        spot_or_normal = 'spot' if spot else 'normal'
        val = await async_redis().get(f'testrun:{self.trid}:{cmd}:duration:{spot_or_normal}')
        return int(val) if val is not None else 0

    async def save(self):
        await async_redis().set(f'testrun:state:{self.trid}', self.json())

    async def notify_build_completed(self):
        resp = await app.httpclient.post(f'/agent/testrun/{self.trid}/build-completed',
                                         content=AgentBuildCompleted(specs=self.specs).json())
        if resp.status_code != 200:
            logger.error(f'Failed to update server that build was completed:'
                         f' {resp.status_code}: {resp.text}')

    async def notify_run_completed(self):
        logger.info(f'Notify run completed: {self.trid}')
        resp = await app.httpclient.post(f'/agent/testrun/{self.trid}/run-completed')
        if resp.status_code != 200:
            logger.error(f'Failed to update testrun duration for testrun {self.trid}: {resp.text}')


async def get_build_state(trid: int, check=False) -> TestRunBuildState:
    st = await async_redis().get(f'testrun:state:{trid}')
    if st:
        return TestRunBuildState.parse_raw(st)
    if check:
        raise BuildFailedException("Missing state")


def check_is_spot(annotations) -> bool:
    if not annotations:
        return False
    autopilot = annotations.get('autopilot.gke.io/selector-toleration')
    if autopilot:
        seltol = json.loads(autopilot)
        for tol in seltol['outputTolerations']:
            if tol['key'] == 'cloud.google.com/gke-spot' and tol['value'] == 'true':
                return True
    return False


def parse_pod_status(pod: V1Pod) -> schemas.PodStatus:
    status: V1PodStatus = pod.status
    metadata: V1ObjectMeta = pod.metadata
    project_id = metadata.labels['project_id']
    testrun_id = metadata.labels['testrun_id']
    annotations = metadata.annotations
    st = schemas.PodStatus(pod_name=metadata.name,
                           project_id=project_id,
                           testrun_id=testrun_id,
                           job_type=metadata.labels['cykubed_job'],
                           phase=status.phase,
                           is_spot=check_is_spot(annotations),
                           start_time=status.start_time)
    if status.phase in ['Succeeded', 'Failed', 'Unknown']:
        st.end_time = utcnow()
        st.duration = int((st.end_time - st.start_time).seconds)
    return st

