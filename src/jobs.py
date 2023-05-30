import asyncio
import datetime
import os
import uuid

import aiofiles
import chevron
import yaml
from chevron import ChevronError
from kubernetes import client, utils as k8utils
from loguru import logger
from yaml import YAMLError

import db
from app import app
from common import schemas
from common.exceptions import InvalidTemplateException, BuildFailedException
from common.redisutils import async_redis
from common.schemas import AgentCloneCompletedEvent, AgentEvent
from common.utils import utcnow
from db import get_testrun, expired_cached_items_iter, get_cached_item, remove_cached_item, add_cached_item, \
    add_build_snapshot_cache_item, get_build_snapshot_cache_item
from k8 import async_get_snapshot, async_delete_pvc, async_delete_snapshot, async_delete_job, async_get_job_status, \
    async_create_snapshot
from settings import settings
from state import TestRunBuildState, get_build_state

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
        await async_create_snapshot(yamlobjects[0])
    except YAMLError as ex:
        raise InvalidTemplateException(f'Invalid YAML in {jobtype} template: {ex}')
    except ChevronError as ex:
        raise InvalidTemplateException(f'Invalid {jobtype} template: {ex}')


async def create_k8_objects(jobtype, context) -> str:
    try:
        k8sclient = client.ApiClient()
        yamlobjects = await render_template(jobtype, context)
        # should only be one object
        kind = yamlobjects[0]['kind']
        name = yamlobjects[0]['metadata']['name']
        logger.info(f'Creating {kind} {name}', id=context['testrun_id'])
        if settings.K8:
            await asyncio.to_thread(k8utils.create_from_yaml, k8sclient,
                                    yaml_objects=yamlobjects, namespace=settings.NAMESPACE)
        else:
            logger.debug(f"K8 disabled: not creating {name}")
        return name
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


async def handle_new_run(testrun: schemas.NewTestRun):
    """
    If there is already a built distribution PVC then go straight to creating the runners
    Otherwise create a build PVC and kick off the clone job
    :param testrun:
    :return:
    """
    # stop existing jobs
    await delete_jobs_for_branch(testrun.id, testrun.branch)
    state = TestRunBuildState(trid=testrun.id, rw_build_pvc=get_new_pvc_name('build-rw'),
                              build_storage=testrun.project.build_storage,
                              node_storage=testrun.project.node_storage)
    await state.save()

    build_snap_cache_item = await get_build_snapshot_cache_item(testrun.sha)
    if build_snap_cache_item and build_snap_cache_item.node_snapshot:
        # this is a rerun of a previous build: use the snapshot to create a build PVC
        # (there should already be a node snapshot)
        node_cache_item = await get_cached_item(build_snap_cache_item.node_snapshot)
        if not node_cache_item:
            # weirdly we have a build snapshot but no associated node one: delete the build snapshot and rebuild
            # everything
            logger.error('Missing node snapshot for cached build: deleting build snapshot')
            await delete_cache_item(node_cache_item)
            await create_clone_job(testrun, state)
        else:
            # otherwise we can directly create the RO PVCs and the runner job
            logger.info(f'Found cached build for sha {testrun.sha}: reuse')
            state.node_snapshot_name = node_cache_item.name
            state.specs = build_snap_cache_item.specs
            state.build_storage = build_snap_cache_item.storage_size
            state.node_storage = node_cache_item.storage_size
            await state.save()
            await create_ro_pvcs_and_runner_job(testrun, state)
    else:
        await create_clone_job(testrun, state)


async def create_ro_pvcs_and_runner_job(testrun, state):
    """
    Create RO build and node PVCs from the snapshots and the build the runner job
    """
    await db.set_specs(testrun, state.specs)
    # create a RO PVC from the build snapshot
    context = common_context(testrun)
    context['snapshot_name'] = get_build_snapshot_name(testrun)
    context['storage'] = state.build_storage
    state.ro_build_pvc = context['ro_pvc_name'] = get_new_pvc_name('build-ro')
    await create_k8_objects('ro-pvc-from-snapshot', context)
    if not state.ro_node_pvc:
        # ditto for the node snapshot if required
        context['snapshot_name'] = state.node_snapshot_name
        context['storage'] = state.node_storage
        state.ro_node_pvc = context['ro_pvc_name'] = get_new_pvc_name('node-ro')
        await create_k8_objects('ro-pvc-from-snapshot', context)
    await state.save()
    # tell the main server
    await state.notify_build_completed()
    # and create the runner job
    await create_runner_job(testrun, state)


async def create_clone_job(testrun, state):
    """
    Create a RW PVC for the build and create the clone job
    """
    context = common_context(testrun)
    context['pvc_name'] = state.rw_build_pvc
    context['storage'] = testrun.project.build_storage
    await create_k8_objects('rw-pvc', context)
    # and clone job
    context['build_pvc_name'] = state.rw_build_pvc
    state.clone_job = await create_k8_objects('clone', context)
    await state.save()
    await app.update_status(testrun.id, 'building')


async def handle_clone_completed(event: AgentCloneCompletedEvent):
    """
    Handle a completed clone. If there are no specs then we'll just mark the test as passed and return.
    Otherwise, we check for an existing node snapshot and create a RO PVC from it, or create a RW PVC in order
    to build the node_modules and later snapshot it
    """
    trid = event.testrun_id
    testrun = await get_testrun(trid)
    if not testrun:
        raise BuildFailedException('Missing testrun')

    state = await get_build_state(trid, True)

    if not event.specs:
        # no specs - default pass
        await app.update_status(trid, 'passed')
        await delete_pvcs_and_jobs(state)
        await state.notify_run_completed()
        return

    state.specs = event.specs
    logger.info(f"Found {len(event.specs)} spec files")

    await db.set_specs(testrun, state.specs)

    if not settings.K8:
        await state.notify_build_completed()
        logger.info(f'Run runner with args "run {testrun.id}"', trid=testrun.id)
        return

    # check for node snapshot
    node_snapshot_name = f'node-{event.cache_key}'
    context = common_context(testrun)
    context['build_pvc_name'] = state.rw_build_pvc
    state.node_snapshot_name = node_snapshot_name

    cached_node_item = await get_cached_snapshot(node_snapshot_name)
    if cached_node_item:
        logger.info(f'Found node cache snapshot {node_snapshot_name}', trid=trid)
        # we have a cached node distribution (i.e a VolumeSnapshot for it) - create a read-only PVC
        state.ro_node_pvc = get_new_pvc_name('node-ro')
        context['storage'] = state.node_storage = cached_node_item.storage_size
        context['snapshot_name'] = node_snapshot_name
        context['node_pvc_name'] = context['ro_pvc_name'] = state.ro_node_pvc
        await create_k8_objects('ro-pvc-from-snapshot', context)
    else:
        # otherwise this will need to build the node dist: create a RW pvc
        state.rw_node_pvc = context['node_pvc_name'] = context['pvc_name'] = get_new_pvc_name('node-rw')
        context['storage'] = state.node_storage
        await create_k8_objects('rw-pvc', context)

    # now create the Job
    state.build_job = await create_k8_objects('build', context)
    await state.save()


def get_build_snapshot_name(testrun):
    return f'build-{testrun.sha}'


async def handle_build_completed(event: AgentEvent):
    """
    Build is completed: create PVCs and snapshots
    :return:
    """
    testrun_id = event.testrun_id
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
            await add_cached_item(state.node_snapshot_name, testrun.project.node_storage)

        # snapshot the build pvc and record it in the cache
        context['snapshot_name'] = get_build_snapshot_name(testrun)
        context['pvc_name'] = state.rw_build_pvc
        await create_k8_snapshot('pvc-snapshot', context)
        await add_build_snapshot_cache_item(testrun.sha, state.node_snapshot_name,
                                            state.specs,
                                            testrun.project.build_storage)

        # finally create the RO pvcs from the snapshots and create the runner job
        await create_ro_pvcs_and_runner_job(testrun, state)

    except Exception as ex:
        logger.exception("Failed to complete the build")
        raise ex


async def create_runner_job(testrun: schemas.NewTestRun, state: TestRunBuildState):
    # next create the runner job: limit the parallism as there's no point having more runners than specs
    context = common_context(testrun)
    context.update(dict(name=f'cykubed-runner-{testrun.project.name}-{testrun.id}-{state.run_job_index}',
                        parallelism=min(testrun.project.parallelism, len(state.specs)),
                        build_pvc_name=state.ro_build_pvc,
                        node_pvc_name=state.ro_node_pvc))
    if not state.runner_deadline:
        state.runner_deadline = utcnow() + datetime.timedelta(seconds=testrun.project.runner_deadline)
    state.run_job = await create_k8_objects('runner', context)
    await state.save()
    await app.update_status(testrun.id, 'running')


async def handle_run_completed(testrun_id):
    """
    Just delete the PVCs
    :param testrun_id:
    """
    logger.info(f'Run {testrun_id} completed')
    state = await get_build_state(testrun_id)
    if state:
        await delete_pvcs_and_jobs(state)
        await state.notify_run_completed()


async def handle_testrun_error(event: schemas.AgentTestRunErrorEvent):
    logger.info(f'Run {event.testrun_id} failed at stage {event.report.stage}')
    await app.httpclient.post(f'/agent/testrun/{event.testrun_id}/error', json=event.report.dict())
    await handle_run_completed(event.testrun_id)


async def prune_cache_loop():
    """
    Pune expired snapshots and PVCs
    :return:
    """
    while app.is_running():
        await prune_cache()
        await asyncio.sleep(300)


async def run_job_tracker():
    r = async_redis()
    logger.info('Running job tracker')
    while app.is_running():
        async for key in r.scan_iter('testrun:state:*', count=100):
            st = await get_build_state(key, False)
            if st and st.run_job and utcnow() < st.runner_deadline:
                # check if the job is finished and we still have remaining specs
                status = await async_get_job_status(st.run_job)
                if status and status.start_time and status.completion_time and not status.active:
                    numspecs = await r.scard(f'testrun:{st.trid}:specs')
                    if numspecs:
                        tr = await get_testrun(st.trid)
                        logger.info(f'Run job {st.trid} is not active but has {numspecs} specs left - recreate it')
                        st.run_job_index += 1
                        # delete the existing job
                        await async_delete_job(st.run_job)
                        # and create a new one
                        await create_runner_job(tr, st)

        await asyncio.sleep(30)


async def delete_cache_item(item):
    # delete volume
    await async_delete_snapshot(item.name)
    await async_redis().delete(f'cache:{item.name}')


async def prune_cache():
    async for item in expired_cached_items_iter():
        await delete_cache_item(item)


async def delete_cached_pvc(name: str):
    await async_delete_pvc(name)
    await remove_cached_item(name)


async def delete_testrun_job(job, trid: int = None):
    logger.info(f"Deleting existing job {job.metadata.name}", trid=trid)
    await async_delete_job(job.metadata.name)
    tr = await get_testrun(trid)
    if tr:
        # just in case the test run failed and didn't clean up, do it here
        await handle_run_completed(trid)


async def delete_jobs_for_branch(trid: int, branch: str):
    if settings.K8:
        # delete any job already running
        api = client.BatchV1Api()
        jobs = api.list_namespaced_job(settings.NAMESPACE, label_selector=f'branch={branch}')
        if jobs.items:
            logger.info(f'Found {len(jobs.items)} existing Jobs - deleting them', trid=trid)
            # delete it (there should just be one, but iterate anyway)
            for job in jobs.items:
                await delete_testrun_job(job, trid)


async def delete_jobs_for_project(project_id):
    api = client.BatchV1Api()
    jobs = api.list_namespaced_job(settings.NAMESPACE, label_selector=f'project_id={project_id}')
    if jobs.items:
        logger.info(f'Found {len(jobs.items)} existing Jobs - deleting them')
        for job in jobs.items:
            await delete_testrun_job(job)


async def delete_jobs(testrun_id: int):
    logger.info(f"Deleting jobs for testrun {testrun_id}")
    api = client.BatchV1Api()
    jobs = api.list_namespaced_job(settings.NAMESPACE, label_selector=f"testrun_id={testrun_id}")
    for job in jobs.items:
        await delete_testrun_job(job, testrun_id)


# async def cancel_testrun(trid: int):
#     """
#     Delete all state (including PVCs)
#     :param trid: test run ID
#     """
#     logger.info(f'Cancel testrun {trid}')
#     if settings.K8:
#         await delete_jobs(trid)
#
#     st = await get_build_state(trid)
#     if st:
#         await st.delete()

async def delete_pvcs_and_jobs(state: TestRunBuildState):
    if settings.K8:
        await async_delete_job(state.clone_job)
        await async_delete_job(state.build_job)
        await async_delete_job(state.run_job)
        await async_delete_pvc(state.rw_build_pvc)
        if state.ro_build_pvc:
            await async_delete_pvc(state.ro_build_pvc)
        if state.rw_node_pvc:
            await async_delete_pvc(state.rw_node_pvc)
        if state.ro_node_pvc:
            await async_delete_pvc(state.ro_node_pvc)


async def clear_cache():
    async for key in async_redis().scan_iter('cache:*'):
        item = await get_cached_item(key[6:], False)
        await delete_cache_item(item)
