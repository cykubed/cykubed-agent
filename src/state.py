import json

from loguru import logger

import db
from app import app
from common import schemas
from common.exceptions import BuildFailedException
from common.redisutils import async_redis
from common.schemas import TestRunBuildState
from settings import settings


async def save_state(state: TestRunBuildState):
    if settings.LOCAL_REDIS:
        await async_redis().set(f'testrun:state:{state.trid}', state.json(), ex=24 * 3600)


async def notify_run_completed(state: TestRunBuildState):
    logger.info(f'Notify run completed: {state.trid}')
    resp = await app.httpclient.post(f'/agent/testrun/{state.trid}/run-completed')
    if resp.status_code != 200:
        logger.error(f'Failed to update testrun duration for testrun {state.trid}: {resp.text}')
    if settings.LOCAL_REDIS:
        await db.cleanup(state.trid)


async def get_build_state(trid: int, check=False) -> TestRunBuildState:
    if settings.LOCAL_REDIS:
        st = await async_redis().get(f'testrun:state:{trid}')
        if st:
            return TestRunBuildState.parse_raw(st)
    else:
        resp = await app.httpclient.get(f'/agent/testrun/{trid}/build-state')
        if resp.status_code == 200:
            return TestRunBuildState.parse_raw(resp.text)

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


async def set_specs(tr: schemas.NewTestRun, specs: list[str]):
    if settings.LOCAL_REDIS:
        await db.set_specs(tr, specs)
    else:
        raise "Not Implemented"


async def notify_build_completed(state: TestRunBuildState):
    resp = await app.httpclient.post(f'/agent/testrun/{state.trid}/build-completed',
                                     content=schemas.AgentBuildCompleted(specs=state.specs).json())
    if resp.status_code != 200:
        logger.error(f'Failed to update server that build was completed:'
                     f' {resp.status_code}: {resp.text}')
