import asyncio
import datetime
import re
import tempfile

from kubernetes_asyncio import watch
from kubernetes_asyncio.client import V1Pod, V1Job, V1JobStatus, V1ObjectMeta, V1PodStatus
from loguru import logger

import db
from app import app
from common import schemas
from common.exceptions import BuildFailedException
from common.k8common import get_batch_api, get_core_api
from common.redisutils import async_redis
from common.schemas import AgentBuildCompletedEvent, CacheItem
from common.utils import utcnow, get_lock_hash
from db import get_testrun, expired_cached_items_iter, get_cached_item, remove_cached_item, \
    add_build_snapshot_cache_item
from k8 import async_get_snapshot, async_delete_pvc, async_delete_snapshot, async_delete_job, wait_for_pvc_ready, \
    create_k8_objects, create_k8_snapshot, wait_for_snapshot_ready
from settings import settings
from state import TestRunBuildState, get_build_state, check_is_spot


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


def create_pvc_name(testrun: schemas.NewTestRun, prefix):
    name = testrun.project.name.lower().replace('_', '-')[:30]
    return f'{name}-{testrun.local_id}-{prefix}'


def create_ro_pvc_name(testrun: schemas.NewTestRun):
    return create_pvc_name(testrun, 'ro')


def create_rw_pvc_name(testrun: schemas.NewTestRun):
    return create_pvc_name(testrun, 'rw')


async def get_build_snapshot_cache_item(sha: str) -> CacheItem:
    key = f'build-{sha}'
    item = await get_cached_item(key)
    if item:
        # check for volume snapshot
        if not await async_get_snapshot(item.name):
            logger.warning(f'Snapshot {item.name} has been deleted - remove cache entry')
            await remove_cached_item(key)
            return None
    return item


async def handle_new_run(testrun: schemas.NewTestRun):
    """
    If there is already a built distribution PVC then go straight to creating the runners.
    If not then perform a sparse checkout to determine the cache key, and use the node snapshot
    as a data source (if available). The create a build job
    :param testrun:
    :return:
    """
    # stop existing jobs
    await app.update_status(testrun.id, 'started')

    await delete_jobs_for_branch(testrun.id, testrun.branch)
    state = TestRunBuildState(trid=testrun.id,
                              build_storage=testrun.project.build_storage)
    await state.save()

    r = async_redis()
    # initialise the completed pods set with an expiry
    await r.sadd(f'testrun:{testrun.id}:completed_pods', 'dummy')
    await r.expire(f'testrun:{testrun.id}:completed_pods', 6 * 3600)

    build_snap_cache_item = await get_build_snapshot_cache_item(testrun.sha)
    if build_snap_cache_item:
        # this is a rerun of a previous build - just create the RO PVC and runner job
        logger.info(f'Found cached build for sha {testrun.sha}: reuse')
        state.ro_build_pvc = create_ro_pvc_name(testrun)
        context = common_context(testrun,
                                 read_only=True, snapshot_name=build_snap_cache_item.name,
                                 pvc_name=state.ro_build_pvc)
        await create_k8_objects('pvc', context)
        state.specs = build_snap_cache_item.specs
        if testrun.project.spec_filter:
            state.specs = [s for s in state.specs if re.search(testrun.project.spec_filter, s)]

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
        state.rw_build_pvc = create_rw_pvc_name(testrun)
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
        await delete_pvcs(state)
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
    state.ro_build_pvc = create_ro_pvc_name(testrun)
    await state.save()
    context.update(dict(pvc_name=state.ro_build_pvc,
                        read_only=True))
    await create_k8_objects('pvc', context)

    # tell the main server
    await state.notify_build_completed()

    # and create the runner job
    await create_runner_job(testrun, state)

    if not state.node_snapshot_name:
        await prepare_cache_wait(state, testrun)


async def prepare_cache_wait(state, testrun):
    """
    Prepare the cache volume with the prepare job (which simply moved the cacheable folders into root and
    deletes the cloned src)
    :param state:
    :param testrun:
    :return:
    """
    logger.info('Wait for RO PVC', trid=testrun.id)

    # we need to create a snapshot

    # wait for the RO PVC to be bound
    await wait_for_pvc_ready(state.ro_build_pvc)

    logger.info('Create prepare cache job', trid=testrun.id)

    # create the prepare job
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
    logger.info(f"Handle cache_prepared event for {testrun_id}")
    testrun = await get_testrun(testrun_id)
    state = await get_build_state(testrun_id, True)
    name = f'node-{state.cache_key}'
    context = common_context(testrun,
                             snapshot_name=name,
                             pvc_name=state.rw_build_pvc)
    await create_k8_snapshot('pvc-snapshot', context)
    await db.add_cached_item(name, state.build_storage)

    # wait for the snashot
    await wait_for_snapshot_ready(name)

    # NOW we can delete the RW PVC
    logger.info(f'Node cache snapshot created: delete build PVC', trid=testrun_id)
    await async_delete_pvc(state.rw_build_pvc)


async def create_runner_job(testrun: schemas.NewTestRun, state: TestRunBuildState):
    # next create the runner job: limit the parallism as there's no point having more runners than specs
    context = common_context(testrun)
    context.update(dict(name=f'runner-{testrun.project.name}-{testrun.local_id}-{state.run_job_index}',
                        parallelism=min(testrun.project.parallelism, len(state.specs)),
                        pvc_name=state.ro_build_pvc))
    if not state.runner_deadline:
        state.runner_deadline = utcnow() + datetime.timedelta(seconds=testrun.project.runner_deadline)
    state.run_job = await create_k8_objects('runner', context)
    await state.save()


async def handle_run_completed(testrun_id):
    """
    Just delete the PVCs
    :param testrun_id:
    """
    logger.info(f'Run {testrun_id} completed')
    state = await get_build_state(testrun_id)
    if state:
        await state.notify_run_completed()
        await delete_pvcs(state)


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


# async def check_jobs_for_testrun(st: TestRunBuildState):
#     logger.debug(f'Check jobs for testrun {st.trid}')
#     r = async_redis()
#     if st.build_job and not st.run_job:
#         status = await async_get_job_status(st.build_job)
#         if status and status.failed:
#             tr = await get_testrun(st.trid)
#             # report it as failed
#             if tr and tr.status != 'failed':
#                 logger.info(f'Build for {st.trid} failed')
#                 errmsg = TestRunErrorReport(stage='build', msg='Build failed')
#                 await handle_testrun_error(schemas.AgentTestRunErrorEvent(testrun_id=st.trid,
#                                                                           report=errmsg))
#                 return
#
#     if st.run_job and utcnow() < st.runner_deadline:
#         # check if the job is finished and we still have remaining specs
#         status = await async_get_job_status(st.run_job)
#         if status and status.start_time and status.completion_time and not status.active:
#             specs = await r.smembers(f'testrun:{st.trid}:specs')
#             if specs:
#                 tr = await get_testrun(st.trid)
#                 numspecs = len(specs)
#                 logger.info(f'Run job {st.trid} is not active but has {numspecs} specs left - recreate it')
#                 st.specs = specs
#                 st.run_job_index += 1
#                 # delete the existing job
#                 await async_delete_job(st.run_job)
#                 # and create a new one
#                 await create_runner_job(tr, st)
#             else:
#                 logger.warning(f'Run job {st.trid} is not active and has no specs remaining - it should have been cleaned up by now?')
#
#
# async def check_jobs():
#     r = async_redis()
#     async for key in r.scan_iter('testrun:state:*', count=100):
#         st = TestRunBuildState.parse_raw(await r.get(key))
#         if st:
#             now = utcnow()
#             if st.runner_deadline and now > st.runner_deadline and \
#                     (now - st.runner_deadline).seconds > settings.TESTRUN_STATE_TTL:
#                 logger.info(f'State for testrun {st.trid} has expired: cleanup and delete state')
#                 await delete_pvcs(st)
#                 await st.notify_run_completed()
#             else:
#                 await check_jobs_for_testrun(st)
#
#
# async def run_job_tracker():
#     logger.info('Running job tracker')
#     while app.is_running():
#         await asyncio.sleep(settings.JOB_TRACKER_PERIOD)
#         await check_jobs()


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


async def delete_pvcs(state: TestRunBuildState):
    # we'll let the RW PVC be deleted by the prepare_cache
    if state.rw_build_pvc and state.node_snapshot_name:
        await async_delete_pvc(state.rw_build_pvc)
    if state.ro_build_pvc:
        await async_delete_pvc(state.ro_build_pvc)

# if state.build_job:
#     await async_delete_job(state.build_job)
# if state.run_job:
#     await async_delete_job(state.run_job)
#


async def clear_cache():
    logger.info('Clearing cache')
    async for key in async_redis().scan_iter('cache:*'):
        item = await get_cached_item(key[6:], False)
        await delete_cache_item(item)


async def upload_testrun_durations(trid):
    r = async_redis()
    podresults = [schemas.PodStatus.parse_raw(x) for x in await r.lrange(f'testrun:{trid}:pod_results', 0, -1)]
    # dedup just in case
    by_pod = list({p.pod_name: p for p in podresults}.values())

    payload = schemas.TestRunDurations(testrun_id=trid)
    for result in by_pod:
        if result.job_type == 'builder':
            payload.total_build_duration += result.duration
        elif result.job_type == 'runner':
            if result.is_spot:
                payload.total_runner_duration_spot += result.duration
            else:
                payload.total_runner_duration += result.duration

    await app.httpclient.post(f'/agent/testrun/{trid}/durations',
                              content=payload.json())


async def recreate_runner_job(st: TestRunBuildState, specs: list[str]):
    tr = await get_testrun(st.trid)
    numspecs = len(specs)
    logger.info(f'Run job {st.trid} is not active but has {numspecs} specs left - recreate it')
    st.specs = specs
    st.run_job_index += 1
    # delete the existing job
    await async_delete_job(st.run_job)
    # and create a new one
    await create_runner_job(tr, st)


async def watch_pod_events():
    v1 = get_core_api()
    while app.is_running():
        async with watch.Watch().stream(v1.list_namespaced_pod,
                                        namespace=settings.NAMESPACE,
                                        label_selector=f"cykubed_job in (runner,builder)",
                                        timeout_seconds=10) as stream:
            while app.is_running():
                async for event in stream:
                    pod: V1Pod = event['object']
                    status: V1PodStatus = pod.status
                    metadata: V1ObjectMeta = pod.metadata
                    if status.phase in ['Succeeded', 'Failed']:
                        # assume finished
                        project_id = metadata.labels['project_id']
                        testrun_id = metadata.labels['testrun_id']
                        annotations = metadata.annotations
                        r = async_redis()
                        if not await r.sismember(f'testrun:{testrun_id}:completed_pods',metadata.name):
                            await r.sadd(f'testrun:{testrun_id}:completed_pods', metadata.name)
                            # send the duration if we haven't already
                            st = schemas.PodDuration(job_type=metadata.labels['cykubed_job'],
                                                     is_spot=check_is_spot(annotations),
                                                     duration=int((utcnow() - status.start_time).seconds))
                            await app.httpclient.post(f'/agent/testrun/{testrun_id}/pod-duration',
                                                      content=st.json())


async def watch_job_events():
    api = get_batch_api()
    r = async_redis()
    while app.is_running():
        async with watch.Watch().stream(api.list_namespaced_job,
                                        namespace=settings.NAMESPACE,
                                        label_selector=f"cykubed_job in (runner,builder)",
                                        timeout_seconds=10) as stream:
            while app.is_running():
                async for event in stream:
                    job: V1Job = event['object']
                    status: V1JobStatus = job.status
                    metadata: V1ObjectMeta = job.metadata
                    labels = metadata.labels
                    trid = labels["testrun_id"]
                    if not status.active:
                        st = await get_build_state(trid)
                        recreating = False
                        if st.run_job and status.completion_time:
                            if utcnow() < st.runner_deadline:
                                # runner job completed under the deadline: check for specs remaining
                                specs = await r.smembers(f'testrun:{st.trid}:specs')
                                if specs:
                                    # yup - recreate the job
                                    await recreate_runner_job(st, specs)

