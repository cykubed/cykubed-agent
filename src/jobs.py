import asyncio
import datetime
import tempfile

from loguru import logger

import db
from app import app
from common import schemas
from common.exceptions import BuildFailedException
from common.k8common import get_batch_api
from common.redisutils import async_redis
from common.schemas import TestRunErrorReport, AgentBuildCompletedEvent
from common.utils import utcnow, get_lock_hash
from db import get_testrun, expired_cached_items_iter, get_cached_item, remove_cached_item, \
    add_build_snapshot_cache_item, get_build_snapshot_cache_item
from k8 import async_get_snapshot, async_delete_pvc, async_delete_snapshot, async_delete_job, async_get_job_status, \
    wait_for_pvc_ready, create_k8_objects, create_k8_snapshot
from settings import settings
from state import TestRunBuildState, get_build_state


def common_context(testrun: schemas.NewTestRun, **kwargs):
    return dict(sha=testrun.sha,
                namespace=settings.NAMESPACE,
                storage_class=settings.STORAGE_CLASS,
                storage=testrun.project.build_storage,
                local_id=testrun.local_id,
                testrun_id=testrun.id,
                testrun=testrun,
                branch=testrun.branch,
                redis_secret_name=settings.REDIS_SECRET_NAME,
                token=settings.API_TOKEN,
                project=testrun.project, **kwargs)


async def get_cached_snapshot(key: str):
    item = await get_cached_item(key, True)
    if item:
        if await async_get_snapshot(item.name):
            return item
        # nope - clean up
        await remove_cached_item(key)

#
# def get_new_pvc_name(prefix: str) -> str:
#     return f'{prefix}-{uuid.uuid4()}'


async def get_cache_key(testrun: schemas.NewTestRun) -> str:
    """
    Perform a sparse checkout to get a yarn.lock or package-lock.json file
    """
    logger.info('Performing sparse clone to determine cache key', trid=testrun.id)
    logger.debug(testrun.json())
    with tempfile.TemporaryDirectory() as tmpdir:
        cmd = f'git clone --depth 1 --branch {testrun.branch} --sparse {testrun.url} . && ' \
              f' git reset --hard {testrun.sha}'

        proc = await asyncio.create_subprocess_shell(cmd, cwd=tmpdir)
        await proc.wait()
        if proc.returncode:
            raise BuildFailedException(f'Failed to clone {testrun.project.name}')

        k = get_lock_hash(tmpdir)
        logger.debug(f'Cache key is {k}')
        return k


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
                              build_storage=testrun.project.build_storage)
    await state.save()

    build_snap_cache_item = await get_build_snapshot_cache_item(testrun.sha)
    if build_snap_cache_item:
        # this is a rerun of a previous build - just create the RO PVC and runner job
        logger.info(f'Found cached build for sha {testrun.sha}: reuse')
        state.ro_build_pvc = f'{testrun.sha[:7]}-ro'
        context = common_context(testrun,
                                 read_only=True, snapshot_name=build_snap_cache_item.name,
                                 pvc_name=state.ro_build_pvc)
        await create_k8_objects('pvc', context)
        state.specs = build_snap_cache_item.specs
        state.build_storage = build_snap_cache_item.storage_size
        await state.save()
        await db.set_specs(testrun, state.specs)
        await state.notify_build_completed()
        await create_runner_job(testrun, state)
    else:
        # First check to see if there is a node cache for this build
        # Perform a sparse checkout to check for the lock file
        cache_key = await get_cache_key(testrun)
        node_snapshot_name = f'node-{cache_key}'
        cached_node_item = await get_cached_snapshot(node_snapshot_name)

        # we need a RW PVC for the build
        state.rw_build_pvc = f'{testrun.sha[:7]}-rw'
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
        await app.update_status(testrun.id, 'building')


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

    # create a snapshot from the build PVC
    context = common_context(testrun,
                             snapshot_name=get_build_snapshot_name(testrun),
                             pvc_name=state.rw_build_pvc)

    # snapshot the build pvc and record it in the cache
    await create_k8_snapshot('pvc-snapshot', context)
    await add_build_snapshot_cache_item(testrun.sha, state.node_snapshot_name,
                                        state.specs,
                                        testrun.project.build_storage)

    # create a RO PVC from this snapshot and a runner that uses it
    state.ro_build_pvc = f'{testrun.sha[:7]}-ro'
    await state.save()
    context.update(dict(pvc_name=state.ro_build_pvc,
                        read_only=True))
    await create_k8_objects('pvc', context)

    # tell the main server
    await state.notify_build_completed()

    # and create the runner job
    await create_runner_job(testrun, state)

    if not state.node_snapshot_name:
        # we need to create a snapshot
        # wait for the RO PVC to be bound
        await wait_for_pvc_ready(state.ro_build_pvc)

        # now create the prepare job - this just deletes the src folder and moves node_modules
        # and cypress_cache to the root folder
        context = common_context(testrun,
                                 command='prepare_cache',
                                 cache_key=state.cache_key,
                                 pvc_name=state.rw_build_pvc)
        name = await create_k8_objects('prepare-cache', context)
        state.prepare_cache_job = name
        await state.save()


async def handle_cache_prepared(testrun_id):
    """
    Create a snapshot from the RW PVC
    """
    testrun = await get_testrun(testrun_id)
    state = await get_build_state(testrun_id, True)
    name=f'node-{state.cache_key}'
    context = common_context(testrun,
                             snapshot_name=name,
                             pvc_name=state.rw_build_pvc)
    await create_k8_snapshot('pvc-snapshot', context)
    await db.add_cached_item(name, state.build_storage)


async def create_runner_job(testrun: schemas.NewTestRun, state: TestRunBuildState):
    # next create the runner job: limit the parallism as there's no point having more runners than specs
    context = common_context(testrun)
    context.update(dict(name=f'cykubed-runner-{testrun.project.name}-{testrun.id}-{state.run_job_index}',
                        parallelism=min(testrun.project.parallelism, len(state.specs)),
                        pvc_name=state.ro_build_pvc))
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
        api = get_batch_api()
        jobs = await api.list_namespaced_job(settings.NAMESPACE, label_selector=f'branch={branch}')
        if jobs.items:
            logger.info(f'Found {len(jobs.items)} existing Jobs - deleting them', trid=trid)
            # delete it (there should just be one, but iterate anyway)
            for job in jobs.items:
                await delete_testrun_job(job, trid)


async def delete_jobs_for_project(project_id):
    api = get_batch_api()
    jobs = await api.list_namespaced_job(settings.NAMESPACE, label_selector=f'project_id={project_id}')
    if jobs.items:
        logger.info(f'Found {len(jobs.items)} existing Jobs - deleting them')
        for job in jobs.items:
            await delete_testrun_job(job)


async def delete_jobs(testrun_id: int):
    logger.info(f"Deleting jobs for testrun {testrun_id}")
    api = get_batch_api()
    jobs = await api.list_namespaced_job(settings.NAMESPACE, label_selector=f"testrun_id={testrun_id}")
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
        if state.prepare_cache_job:
            await async_delete_job(state.prepare_cache_job)

        await async_delete_pvc(state.rw_build_pvc)
        if state.ro_build_pvc:
            await async_delete_pvc(state.ro_build_pvc)


async def clear_cache():
    async for key in async_redis().scan_iter('cache:*'):
        item = await get_cached_item(key[6:], False)
        await delete_cache_item(item)


