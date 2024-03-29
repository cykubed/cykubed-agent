import json

from loguru import logger

from app import app
from common import schemas
from common.exceptions import BuildFailedException
from common.schemas import TestRunBuildState


async def save_build_state(state: TestRunBuildState):
    resp = await app.httpclient.put(f'/agent/testrun/{state.testrun_id}/build-state',
                        content=state.json())
    if resp.status_code != 200:
        raise BuildFailedException("Failed to save build state - bailing out")



async def get_build_state(trid: int, check=False) -> TestRunBuildState:
    resp = await app.httpclient.get(f'/agent/testrun/{trid}/build-state')
    if resp.status_code == 200:
        return TestRunBuildState.parse_raw(resp.text)

    if check:
        raise BuildFailedException("Missing state")


async def delete_build_state(trid: int):
    resp = await app.httpclient.delete(f'/agent/testrun/{trid}/build-state')
    if resp.status_code != 200:
        logger.error(f'Failed to delete build state: {resp.status_code}: {resp.text}')


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


async def notify_build_completed(state: TestRunBuildState):
    resp = await app.httpclient.post(f'/agent/testrun/{state.testrun_id}/build-completed',
                                     content=schemas.AgentBuildCompleted(specs=state.specs).json())
    if resp.status_code != 200:
        logger.error(f'Failed to update server that build was completed:'
                     f' {resp.status_code}: {resp.text}')
