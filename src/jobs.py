import asyncio
import os
import uuid
from typing import Optional

import aiofiles
import chevron
import yaml
from chevron import ChevronError
from kubernetes import client, utils as k8utils
from kubernetes.client import ApiException
from loguru import logger
from pydantic import BaseModel
from yaml import YAMLError

from app import app
from common import schemas
from common.exceptions import InvalidTemplateException, BuildFailedException
from common.k8common import get_core_api, get_custom_api
from common.redisutils import async_redis
from common.schemas import AgentCloneCompletedEvent
from db import get_testrun, expired_cached_items_iter, get_cached_item, remove_cached_item, add_cached_item, \
    add_build_snapshot_cache_item, get_build_snapshot_cache_item
from settings import settings

TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), 'k8config', 'templates')


def get_template_path(name: str) -> str:
    return os.path.join(TEMPLATES_DIR, f'{name}.mustache')


async def get_job_template(name: str) -> str:
    async with aiofiles.open(get_template_path(name), mode='r') as f:
        return await f.read()


def common_context(testrun: schemas.NewTestRun):
    return dict(sha=testrun.sha,
                namespace=settings.NAMESPACE,
                storage_class=settings.STORAGE_CLASS,
                local_id=testrun.local_id,
                testrun_id=testrun.id,
                testrun=testrun,
                branch=testrun.branch,
                redis_secret_name=settings.REDIS_SECRET_NAME,
                token=settings.API_TOKEN,
                storage=testrun.project.build_ephemeral_storage,
                project=testrun.project)


async def render_template(jobtype, context):
    template = await get_job_template(jobtype)
    jobyaml = chevron.render(template, context)
    return list(yaml.safe_load_all(jobyaml))


async def create_k8_snapshot(jobtype, context):
    """
    Annoyingly volume snapsnhots have to use the Custom API
    :param jobtype:
    :param context:
    :return:
    """
    try:
        yamlobjects = await render_template(jobtype, context)
        get_custom_api().create_namespaced_custom_object(group="snapshot.storage.k8s.io",
                                                         version="v1",
                                                         namespace=settings.NAMESPACE,
                                                         plural="volumesnapshots",
                                                         body=yamlobjects[0])
    except YAMLError as ex:
        raise InvalidTemplateException(f'Invalid YAML in {jobtype} template: {ex}')
    except ChevronError as ex:
        raise InvalidTemplateException(f'Invalid {jobtype} template: {ex}')

#
# This is too bloody overcomplicated Nick
# Simplify: just generate the names as UUIDs and store them in Redis
# Don't try to reuse - just delete everything at the end and let the Redis cache catch the stragglers
#

async def create_k8_objects(jobtype, context):
    try:
        k8sclient = client.ApiClient()
        yamlobjects = await render_template(jobtype, context)
        # should only be one object
        kind = yamlobjects[0]['kind']
        name = yamlobjects[0]['metadata']['name']
        logger.info(f'Creating {kind} {name}', id=context['testrun_id'])
        await asyncio.to_thread(k8utils.create_from_yaml, k8sclient,
                                yaml_objects=yamlobjects, namespace=settings.NAMESPACE)
    except YAMLError as ex:
        raise InvalidTemplateException(f'Invalid YAML in {jobtype} template: {ex}')
    except ChevronError as ex:
        raise InvalidTemplateException(f'Invalid {jobtype} template: {ex}')
    except Exception as ex:
        logger.exception(f"Failed to create {jobtype}")
        raise ex


async def get_cached_snapshot(key: str):
    item = await get_cached_item(key, True)
    if item:
        if await async_get_snapshot(item.name):
            return item
        # nope - clean up
        await remove_cached_item(key)


def get_new_pvc_name(prefix: str) -> str:
    return f'{prefix}-{uuid.uuid4()}'


class TestRunBuildState(BaseModel):
    trid: int
    specs: list[str] = []
    parallelism: Optional[int]
    node_snapshot_name: Optional[str]
    rw_build_pvc: str
    rw_node_pvc: Optional[str]
    ro_build_pvc: Optional[str]
    ro_node_pvc: Optional[str]

    async def delete(self):
        await async_delete_pvc(self.rw_build_pvc)
        if self.ro_build_pvc:
            await async_delete_pvc(self.ro_build_pvc)
        if self.rw_node_pvc:
            await async_delete_pvc(self.rw_node_pvc)
        if self.ro_node_pvc:
            await async_delete_pvc(self.ro_node_pvc)
        r = async_redis()
        await r.delete(f'testrun:{self.trid}:state')
        await r.delete(f'testrun:{self.trid}:specs')
        await r.delete(f'testrun:{self.trid}')
        await r.srem('testruns', str(self.trid))


async def set_build_state(state: TestRunBuildState):
    await async_redis().set(f'testrun:{state.trid}:state', state.json())


async def get_build_state(trid: int, check=False) -> TestRunBuildState:
    st = await async_redis().get(f'testrun:{trid}:state')
    if st:
        return TestRunBuildState.parse_raw(st)
    if check:
        raise BuildFailedException("Missing state")


async def handle_new_run(testrun: schemas.NewTestRun):
    """
    If there is already a built distribution PVC then go straight to creating the runners
    Otherwise create a build PVC and kick off the clone job
    :param testrun:
    :return:
    """
    # stop existing jobs
    await delete_jobs_for_branch(testrun.id, testrun.branch)
    state = TestRunBuildState(trid=testrun.id, rw_build_pvc=get_new_pvc_name('build'))
    await async_redis().set(f'testrun:{testrun.id}:state', state.json())

    build_snap_cache_item = await get_build_snapshot_cache_item(testrun.sha)
    if build_snap_cache_item and build_snap_cache_item.node_snapshot:
        # this is a rerun of a previous build: use the snapshot to create a build PVC
        # (there should already be a node snapshot)
        state.node_snapshot_name = build_snap_cache_item.node_snapshot
        state.specs = build_snap_cache_item.specs
        # create a RO PVC from the build snapshot
        context = common_context(testrun)
        context['snapshot_name'] = build_snap_cache_item.name
        state.ro_build_pvc = context['ro_pvc_name'] = get_new_pvc_name('build-ro')
        await create_k8_objects('ro-pvc-from-snapshot', context)
        # ditto for the node snapshot
        context['snapshot_name'] = state.node_snapshot_name
        state.ro_node_pvc = context['ro_pvc_name'] = get_new_pvc_name('node-ro')
        await set_build_state(state)
        await create_k8_objects('ro-pvc-from-snapshot', context)
        # and create the runner job
        await create_runner_job(testrun, state)
    else:
        # otherwise we'll need to clone and build as usual
        context = common_context(testrun)
        context['pvc_name'] = state.rw_build_pvc
        await create_k8_objects('rw-pvc', context)
        # and clone
        context['build_pvc_name'] = state.rw_build_pvc
        await create_k8_objects('clone', context)
        await app.update_status(testrun.id, 'building')


async def handle_clone_completed(event: AgentCloneCompletedEvent):
    """
    Handle a completed clone. If there are no specs then we'll just mark the test as passed and return.
    Otherwise, we check for an existing node snapshot and create a RO PVC from it, or create a RW PVC in order
    to build the node_modules and later snapshot it
    """
    trid = event.testrun_id
    state = await get_build_state(trid, True)

    if not event.specs:
        # no specs - default pass
        await app.update_status(trid, 'passed')
        await state.delete()
        return

    testrun = await get_testrun(trid)
    if not testrun:
        raise BuildFailedException('Missing testrun')
    state.specs = event.specs

    # check for node snapshot
    node_snapshot_name = f'node-{event.cache_key}'
    context = common_context(testrun)
    context['build_pvc_name'] = state.rw_build_pvc

    if await get_cached_snapshot(node_snapshot_name):
        logger.info(f'Found node cache snapshot {node_snapshot_name}', trid=trid)
        # we have a cached node distribution (i.e a VolumeSnapshot for it) - create a read-only PVC
        state.ro_node_pvc = get_new_pvc_name('node-ro')
        context['snapshot_name'] = node_snapshot_name
        context['node_pvc_name'] = context['ro_pvc_name'] = state.ro_node_pvc
        context['storage'] = testrun.project.build_ephemeral_storage
        await create_k8_objects('ro-pvc-from-snapshot', context)
        await set_build_state(state)
    else:
        # otherwise this will need to build the node dist: create a RW pvc
        state.rw_node_pvc = context['node_pvc_name'] = context['pvc_name'] = get_new_pvc_name('node-rw')
        state.node_snapshot_name = node_snapshot_name
        await set_build_state(state)
        context['storage'] = testrun.project.build_ephemeral_storage
        await create_k8_objects('rw-pvc', context)

    # now create the Job
    await create_k8_objects('build', context)


async def build_completed(testrun_id: int):
    """
    Build is completed, so
    :param testrun_id:
    :return:
    """
    logger.info(f'Build completed for testrun {testrun_id}')
    try:
        testrun = await get_testrun(testrun_id)
        state = await get_build_state(testrun_id, True)
        context = common_context(testrun)
        if state.rw_node_pvc:
            # take a snapshot of the node dist
            context['snapshot_name'] = state.node_snapshot_name
            context['pvc_name'] = state.rw_node_pvc
            await create_k8_snapshot('pvc-snapshot', context)
            await add_cached_item(state.node_snapshot_name)
            # and create a RO PVC from it
            state.ro_node_pvc = context['ro_pvc_name'] = context['pvc_name'] = get_new_pvc_name('node-ro')
            await set_build_state(state)
            await create_k8_objects('ro-pvc-from-snapshot', context)

        # snapshot the build pvc
        build_snapshot_name = context['snapshot_name'] = f'build-{testrun.sha}'
        context['pvc_name'] = state.rw_build_pvc
        await create_k8_snapshot('pvc-snapshot', context)
        await add_build_snapshot_cache_item(build_snapshot_name, state.node_snapshot_name, state.specs)

        # create a many-read-only volume from the snapshot
        state.ro_build_pvc = context['ro_pvc_name'] = get_new_pvc_name('build-ro')
        await set_build_state(state)
        await create_k8_objects('ro-pvc-from-snapshot', context)

        # finally create the runner job
        await create_runner_job(testrun, state)

    except Exception as ex:
        logger.exception("Failed to complete the build")
        raise ex


async def create_runner_job(testrun: schemas.NewTestRun, state: TestRunBuildState):
    # next create the runner job: limit the parallism as there's no point having more runners than specs
    context = common_context(testrun)
    context.update(dict(name=f'cykubed-runner-{testrun.project.name}-{testrun.id}',
                        parallelism=min(testrun.project.parallelism, len(state.specs)),
                        build_pvc_name=state.ro_build_pvc,
                        node_pvc_name=state.ro_node_pvc))
    await create_k8_objects('runner', context)


async def handle_run_completed(testrun_id):
    """
    Just delete the PVCs
    :param testrun_id:
    """
    state = await get_build_state(testrun_id)
    if state:
        await state.delete()


async def prune_cache_loop():
    """
    Pune expired snapshots and PVCs
    :return:
    """
    while app.is_running():
        # await prune_cache()
        await asyncio.sleep(300)


async def delete_cache_item(item):
    # delete volume
    await async_delete_snapshot(item.name)


async def prune_cache():
    async for item in expired_cached_items_iter():
        await delete_cache_item(item)


def delete_pvc(name: str):
    try:
        get_core_api().delete_namespaced_persistent_volume_claim(name, settings.NAMESPACE)
    except ApiException as ex:
        if ex.status != 404:
            logger.exception('Failed to delete PVC')


async def delete_cached_pvc(name: str):
    await async_delete_pvc(name)
    await remove_cached_item(name)


def get_pvc(pvc_name: str) -> bool:
    # check if the PVC exists
    try:
        return get_core_api().read_namespaced_persistent_volume_claim(pvc_name, settings.NAMESPACE)
    except ApiException as ex:
        if ex.status == 404:
            return False
        else:
            raise BuildFailedException('Failed to determine existence of build PVC')


def delete_snapshot(name: str):
    try:
        get_custom_api().delete_namespaced_custom_object(group="snapshot.storage.k8s.io",
                                                    version="v1beta1",
                                                    namespace=settings.NAMESPACE,
                                                    plural="volumesnapshots",
                                                    name=name)
    except ApiException as ex:
        if ex.status == 404:
            # already deleted - ignore
            pass
        else:
            logger.exception(f'Failed to delete snapshot')
            raise BuildFailedException(f'Failed to delete snapshot')


def get_snapshot(name: str):
    try:
        return get_custom_api().get_namespaced_custom_object(group="snapshot.storage.k8s.io",
                                            version="v1beta1",
                                            namespace=settings.NAMESPACE,
                                            plural="volumesnapshots",
                                            name=name)
    except ApiException as ex:
        if ex.status == 404:
            return False
        else:
            raise BuildFailedException('Failed to determine existence of snapshot')


async def async_get_snapshot(name: str):
    return await asyncio.to_thread(get_snapshot, name)


async def async_delete_pvc(name: str):
    await asyncio.to_thread(delete_pvc, name)


async def async_delete_snapshot(name: str):
    await asyncio.to_thread(delete_snapshot, name)


async def async_get_pvc(name: str):
    return await asyncio.to_thread(get_pvc, name)


async def delete_job(job, trid: int = None):
    logger.info(f"Deleting existing job {job.metadata.name}", trid=trid)
    client.BatchV1Api().delete_namespaced_job(job.metadata.name, settings.NAMESPACE)
    poditems = get_core_api().list_namespaced_pod(settings.NAMESPACE,
                                                  label_selector=f"job-name={job.metadata.name}").items
    if poditems:
        for pod in poditems:
            logger.info(f'Deleting pod {pod.metadata.name}', id=trid)
            get_core_api().delete_namespaced_pod(pod.metadata.name, settings.NAMESPACE)

    tr = await get_testrun(trid)
    if tr:
        # just in case the test run failed and didn't clean up, do it here
        await handle_run_completed(trid)


async def delete_jobs_for_branch(trid: int, branch: str):
    # delete any job already running
    api = client.BatchV1Api()
    jobs = api.list_namespaced_job(settings.NAMESPACE, label_selector=f'branch={branch}')
    if jobs.items:
        logger.info(f'Found {len(jobs.items)} existing Jobs - deleting them', trid=trid)
        # delete it (there should just be one, but iterate anyway)
        for job in jobs.items:
            await delete_job(job, trid)


async def delete_jobs_for_project(project_id):
    api = client.BatchV1Api()
    jobs = api.list_namespaced_job(settings.NAMESPACE, label_selector=f'project_id={project_id}')
    if jobs.items:
        logger.info(f'Found {len(jobs.items)} existing Jobs - deleting them')
        for job in jobs.items:
            await delete_job(job)


async def delete_jobs(testrun_id: int):
    logger.info(f"Deleting jobs for testrun {testrun_id}")
    api = client.BatchV1Api()
    jobs = api.list_namespaced_job(settings.NAMESPACE, label_selector=f"testrun_id={testrun_id}")
    for job in jobs.items:
        await delete_job(job, testrun_id)


def is_pod_running(podname: str):
    v1 = client.CoreV1Api()
    try:
        v1.read_namespaced_pod(podname, settings.NAMESPACE)
        return True
    except ApiException:
        return False


async def cancel_testrun(trid: int):
    """
    Delete all state (including PVCs)
    :param trid: test run ID
    """
    await delete_jobs(trid)
    st = await get_build_state(trid)
    if st:
        await st.delete()


async def clear_cache():
    async for key in async_redis().scan_iter('cache:*'):
        item = await get_cached_item(key[6:], False)
        await delete_cache_item(item)
