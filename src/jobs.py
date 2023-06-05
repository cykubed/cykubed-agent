import asyncio
import datetime
import os
import tempfile
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
from common.schemas import  TestRunErrorReport, AgentBuildCompletedEvent
from common.utils import utcnow, get_lock_hash
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


def common_context(testrun: schemas.NewTestRun, **kwargs):
    return dict(sha=testrun.sha,
                namespace=settings.NAMESPACE,
                storage_class=settings.STORAGE_CLASS,
                local_id=testrun.local_id,
                testrun_id=testrun.id,
                testrun=testrun,
                branch=testrun.branch,
                redis_secret_name=settings.REDIS_SECRET_NAME,
                token=settings.API_TOKEN,
                project=testrun.project, **kwargs)


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


async def get_cache_key(testrun: schemas.NewTestRun) -> str:
    """
    Perform a sparse checkout to get a yarn.lock or package-lock.json file
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        cmd = f'git clone --depth 1 --branch {testrun.branch} --no-checkout {testrun.url} && ' \
              f' git reset --hard {testrun.sha} && ' \
              f' git sparse-checkout set yarn.lock package-lock.json'

        proc = await asyncio.create_subprocess_shell(cmd, cwd=tmpdir)
        await proc.wait()
        if proc.returncode:
            raise BuildFailedException(f'Failed to clone {testrun.project.name}')

        return get_lock_hash(tmpdir)


async def handle_new_run(testrun: schemas.NewTestRun):
    """
    If there is already a built distribution PVC then go straight to creating the runners.
    If not then perform a sparse checkout to determine the cache key, and use the node snapshot
    as a data source (if available). The create a build job
    :param testrun:
    :return:
    """
    # stop existing jobs
    await delete_jobs_for_branch(testrun.id, testrun.branch)
    state = TestRunBuildState(trid=testrun.id,
                              rw_build_pvc=get_new_pvc_name('build-rw'),
                              build_storage=testrun.project.build_storage)
    await state.save()

    build_snap_cache_item = await get_build_snapshot_cache_item(testrun.sha)
    if build_snap_cache_item:
        # this is a rerun of a previous build - just create the RO PVC and runner job
        logger.info(f'Found cached build for sha {testrun.sha}: reuse')
        context = common_context(testrun,
                                 read_only=True, snapshot_name=build_snap_cache_item.name,
                                 pvc_name=get_new_pvc_name('ro'))
        await create_k8_objects('pvc', context)
        state.specs = build_snap_cache_item.specs
        state.build_storage = build_snap_cache_item.storage_size
        await state.save()
        await create_runner_job(testrun, state)
    else:
        # First check to see if there is a node cache for this build
        # Perform a sparse checkout to check for the lock file
        cache_key = await get_cache_key(testrun)
        node_snapshot_name = f'node-{cache_key}'
        cached_node_item = await get_cached_snapshot(node_snapshot_name)

        # we need a RW PVC for the build
        state.rw_build_pvc = get_new_pvc_name('rw')
        state.cache_key = cache_key
        context = common_context(testrun,
                                 pvc_name=state.rw_build_pvc)
        # base it on the node cache if we have one
        if cached_node_item:
            state.node_snapshot_name = context['snapshot_name'] = cached_node_item.name
        await create_k8_objects('pvc', context)
        # and create the build job
        state.build_job = await create_k8_objects('build', context)
        await state.save()


def get_build_snapshot_name(testrun):
    return f'build-{testrun.sha}'


async def handle_build_completed(event: AgentBuildCompletedEvent):
    """
    Build is completed: create PVCs and snapshots
    :return:
    """
    testrun_id = event.testrun_id
    logger.info(f'Build completed for testrun {testrun_id}')

    testrun = await get_testrun(testrun_id)
    state = await get_build_state(testrun_id, True)

    if not event.specs:
        # no specs - default pass
        await app.update_status(testrun_id, 'passed')
        await delete_pvcs_and_jobs(state)
        await state.notify_run_completed()
        return

    state.specs = event.specs
    logger.info(f"Found {len(event.specs)} spec files")

    await db.set_specs(testrun, state.specs)

    # tell the main server
    await state.notify_build_completed()

    context = common_context(testrun,
                             snapshot_name=get_build_snapshot_name(testrun),
                             pvc_name=state.rw_build_pvc)

    # snapshot the build pvc and record it in the cache
    await create_k8_snapshot('pvc-snapshot', context)
    await add_build_snapshot_cache_item(testrun.sha, state.node_snapshot_name,
                                        state.specs,
                                        testrun.project.build_storage)

    # create a RO PVC from this snapshot and a runner that uses it
    state.ro_build_pvc = get_new_pvc_name('build-ro')
    context.update(dict(pvc_name=state.ro_build_pvc,
                        read_only=True))
    await create_k8_objects('pvc', context)

    # and create the runner job
    await create_runner_job(testrun, state)

    # best approach now is probably to wait for RO PVC to be created, and then kick off a job to prepare the cache

    if not state.node_snapshot_name:
        # we need to prepare the cache - make another PVC to avoid race conditions
        #  Either wait for RO PVC to be created
        context = common_context(testrun,
                                 snapshot_name=get_build_snapshot_name(testrun),
                                 pvc_name=state.rw_build_pvc)


async def create_ro_pvc_and_runner_job(testrun, state):
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
    await state.save()



async def create_runner_job(testrun: schemas.NewTestRun, state: TestRunBuildState):
    # next create the runner job: limit the parallism as there's no point having more runners than specs
    context = common_context(testrun)
    context.update(dict(name=f'cykubed-runner-{testrun.project.name}-{testrun.id}-{state.run_job_index}',
                        parallelism=min(testrun.project.parallelism, len(state.specs)),
                        build_pvc_name=state.ro_build_pvc))
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


async def check_jobs_for_testrun(st: TestRunBuildState):
    logger.debug(f'Check jobs for testrun {st.trid}')
    r = async_redis()
    if st.build_job and not st.run_job:
        status = await async_get_job_status(st.build_job)
        if status and status.failed:
            tr = await get_testrun(st.trid)
            # report it as failed
            if tr and tr.status != 'failed':
                logger.info(f'Build for {st.trid} failed')
                errmsg = TestRunErrorReport(stage='build', msg='Build failed')
                await handle_testrun_error(schemas.AgentTestRunErrorEvent(testrun_id=st.trid,
                                                                          report=errmsg))
                return

    if st.run_job and utcnow() < st.runner_deadline:
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


async def check_jobs():
    r = async_redis()
    async for key in r.scan_iter('testrun:state:*', count=100):
        st = TestRunBuildState.parse_raw(await r.get(key))
        if st:
            await check_jobs_for_testrun(st)


async def run_job_tracker():
    logger.info('Running job tracker')
    while app.is_running():
        await asyncio.sleep(30)
        await check_jobs()


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
        if state.build_job:
            await async_delete_job(state.build_job)
        if state.run_job:
            await async_delete_job(state.run_job)
        await async_delete_pvc(state.rw_build_pvc)
        if state.ro_build_pvc:
            await async_delete_pvc(state.ro_build_pvc)


async def clear_cache():
    async for key in async_redis().scan_iter('cache:*'):
        item = await get_cached_item(key[6:], False)
        await delete_cache_item(item)
