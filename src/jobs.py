import asyncio
import datetime
import tempfile

from loguru import logger

import cache
import db
from app import app
from cache import get_cached_item, add_build_snapshot_cache_item, remove_cached_item
from common import schemas
from common.enums import PLATFORMS_SUPPORTING_SPOT
from common.exceptions import BuildFailedException
from common.k8common import get_batch_api
from common.redisutils import async_redis
from common.schemas import AgentBuildCompletedEvent, CacheItem
from common.utils import utcnow, get_lock_hash
from db import get_testrun
from k8utils import async_get_snapshot, async_delete_pvc, async_delete_job, create_k8_objects, create_k8_snapshot, \
    wait_for_snapshot_ready, render_template
from settings import settings
from state import TestRunBuildState, get_build_state


def get_spot_config(spot_percentage: int) -> str:
    if spot_percentage and settings.PLATFORM in PLATFORMS_SUPPORTING_SPOT:
        context = dict(spot_percentage=spot_percentage)
        if settings.PLATFORM == 'gke' and spot_percentage < 100:
            context['use_spot_affinity'] = True
        spot = render_template(f'spot/{settings.PLATFORM}', context)
    else:
        spot = ""
    return spot


def use_read_only_pvc(testrun: schemas.NewTestRun) -> bool:
    return settings.use_read_only_many and not testrun.project.server_cmd


def common_context(testrun: schemas.NewTestRun, **kwargs):
    return dict(sha=testrun.sha,
                priority_class=settings.PRIORITY_CLASS,
                snapshot_class_name=settings.VOLUME_SNAPSHOT_CLASS,
                namespace=settings.NAMESPACE,
                image=testrun.project.docker_image.image,
                storage_class=settings.STORAGE_CLASS,
                storage=testrun.project.build_storage,
                local_id=testrun.local_id,
                testrun_id=testrun.id,
                testrun=testrun,
                branch=testrun.branch,
                redis_secret_name=settings.REDIS_SECRET_NAME,
                token=settings.API_TOKEN,
                preprovision=testrun.preprovision,
                parallelism=testrun.project.parallelism,
                read_only_pvc=use_read_only_pvc(testrun),
                use_spot_affinity=(settings.PLATFORM == 'aks' or
                                  (settings.PLATFORM == 'gke' and (0 < testrun.spot_percentage < 100))),
                gke=(settings.PLATFORM == 'gke'),
                aks=(settings.PLATFORM == 'aks'),
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
    return f'{testrun.project.organisation_id}-{name}-{testrun.local_id}-{prefix}'


def create_ro_pvc_name(testrun: schemas.NewTestRun):
    return create_pvc_name(testrun, 'ro')


def create_rw_pvc_name(testrun: schemas.NewTestRun):
    return create_pvc_name(testrun, 'rw')


async def get_build_snapshot_cache_item(testrun: schemas.NewTestRun) -> CacheItem:
    key = get_build_snapshot_name(testrun)
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

    await delete_jobs_for_branch(testrun)
    state = TestRunBuildState(trid=testrun.id,
                              project_id=testrun.project.id,
                              build_storage=testrun.project.build_storage)
    await state.save()

    r = async_redis()
    # initialise the completed pods set with an expiry
    await r.sadd(f'testrun:{testrun.id}:completed_pods', 'dummy')
    await r.expire(f'testrun:{testrun.id}:completed_pods', 6 * 3600)

    build_snap_cache_item = await get_build_snapshot_cache_item(testrun)
    if build_snap_cache_item:
        # this is a rerun of a previous build - just create the RO PVC and runner job
        logger.info(f'Found cached build for sha {testrun.sha}: reuse', trid=testrun.id)
        if use_read_only_pvc(testrun):
            state.ro_build_pvc = create_ro_pvc_name(testrun)
        else:
            state.build_snapshot_name = build_snap_cache_item.name
        state.specs = build_snap_cache_item.specs
        state.build_storage = build_snap_cache_item.storage_size
        await state.save()

        if use_read_only_pvc(testrun):
            context = common_context(testrun,
                                     read_only=True, snapshot_name=build_snap_cache_item.name,
                                     pvc_name=state.ro_build_pvc)
            await create_k8_objects('pvc', context)

        await db.set_specs(testrun, state.specs)
        await state.notify_build_completed()
        await create_runner_job(testrun, state)
    else:
        await create_build_job(testrun, state)


async def create_build_job(testrun: schemas.NewTestRun, state: TestRunBuildState):
    logger.info(f'Create build job for testrun {testrun.local_id}', trid=testrun.id)
    # First check to see if there is a node cache for this build
    # Perform a sparse checkout to check for the lock file
    cache_key = await get_cache_key(testrun)
    node_snapshot_name = f'{testrun.project.organisation_id}-node-{cache_key}'
    cached_node_item = await get_cached_snapshot(node_snapshot_name)

    # we need a RW PVC for the build
    state.rw_build_pvc = create_rw_pvc_name(testrun)
    state.cache_key = cache_key
    await state.save()

    context = common_context(testrun,
                             pvc_name=state.rw_build_pvc)
    if context['preprovision']:
        logger.debug('Create pre-provision job')
        state.preprovision_job = await create_k8_objects('pre-provision', context)

    # base it on the node cache if we have one
    if cached_node_item:
        state.node_snapshot_name = context['snapshot_name'] = cached_node_item.name

    await create_k8_objects('pvc', context)
    # and create the build job
    context['job_name'] = f'{testrun.project.organisation_id}-builder-{testrun.project.name}-{testrun.local_id}'
    if testrun.spot_percentage > 0:
        # all or nothing for the build
        context['spot'] = get_spot_config(100)
    state.build_job = await create_k8_objects('build', context)
    await state.save()
    await app.update_status(testrun.id, 'building')


def get_build_snapshot_name(testrun):
    return f'{testrun.project.organisation_id}-build-{testrun.sha}'


async def handle_build_completed(event: AgentBuildCompletedEvent):
    """
    Build is completed: create PVCs and snapshots
    :return:
    """
    testrun_id = event.testrun_id
    logger.info(f'Build completed', trid=testrun_id)

    testrun = await get_testrun(testrun_id)
    st = await get_build_state(testrun_id, True)

    if not event.specs:
        # no specs - default pass
        await app.update_status(testrun_id, 'passed')
        await delete_pvcs(st)
        await st.notify_run_completed()
        return

    st.specs = event.specs
    logger.info(f"Found {len(event.specs)} spec files")

    await db.set_specs(testrun, st.specs)

    # create a snapshot from the build PVC
    st.build_snapshot_name = get_build_snapshot_name(testrun)
    context = common_context(testrun,
                             snapshot_name=st.build_snapshot_name,
                             pvc_name=st.rw_build_pvc)

    if st.preprovision_job:
        logger.debug('Delete pre-provision job')
        await async_delete_job(st.preprovision_job)

    logger.info(f'Create build snapshot', trid=testrun_id)
    await create_k8_snapshot('pvc-snapshot', context)
    await st.save()
    await add_build_snapshot_cache_item(testrun.project.organisation_id,
                                        testrun.sha,
                                        st.specs,
                                        testrun.project.build_storage)

    await wait_for_snapshot_ready(st.build_snapshot_name)

    if use_read_only_pvc(testrun):
        # create a RO PVC from this snapshot and a runner that uses it
        st.ro_build_pvc = create_ro_pvc_name(testrun)
        await st.save()
        context.update(dict(pvc_name=st.ro_build_pvc,
                            read_only=True))
        await create_k8_objects('pvc', context)

    logger.info(f'Build snapshot ready', trid=testrun_id)

    # tell the main server
    await st.notify_build_completed()

    # and create the runner job
    await create_runner_job(testrun, st)

    if not st.node_snapshot_name:
        await prepare_cache_wait(st, testrun)


async def prepare_cache_wait(state, testrun):
    """
    Prepare the cache volume with the prepare job (which simply moved the cacheable folders into root and
    deletes the cloned src)
    :param state:
    :param testrun:
    :return:
    """
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
    testrun: schemas.NewTestRun = await get_testrun(testrun_id)
    state = await get_build_state(testrun_id, True)
    name = f'{testrun.project.organisation_id}-node-{state.cache_key}'
    context = common_context(testrun,
                             snapshot_name=name,
                             cache_key=state.cache_key,
                             pvc_name=state.rw_build_pvc)
    await create_k8_snapshot('pvc-snapshot', context)
    await cache.add_cached_item(testrun.project.organisation_id, name, state.build_storage)

    # wait for the snashot
    await wait_for_snapshot_ready(name)

    # NOW we can delete the RW PVC
    logger.info(f'Node cache snapshot created: delete build PVC', trid=testrun_id)
    await async_delete_pvc(state.rw_build_pvc)


async def create_runner_job(testrun: schemas.NewTestRun, state: TestRunBuildState):
    # next create the runner job: limit the parallism as there's no point having more runners than specs
    context = common_context(testrun)
    context.update(
        dict(name=f'{testrun.project.organisation_id}-runner-{testrun.project.name}-{testrun.local_id}-{state.run_job_index}',
             parallelism=min(testrun.project.parallelism, len(state.specs)),
             build_snapshot_name=state.build_snapshot_name,
             pvc_name=state.ro_build_pvc))
    if settings.PLATFORM in PLATFORMS_SUPPORTING_SPOT and testrun.spot_percentage > 0:
        # no spot on this platform
        context['spot'] = get_spot_config(testrun.spot_percentage)
    if not state.runner_deadline:
        state.runner_deadline = utcnow() + datetime.timedelta(seconds=testrun.project.runner_deadline)
    state.run_job = await create_k8_objects('runner', context)
    await state.save()


async def handle_run_completed(testrun_id: int, delete_pvcs_only=True):
    """
    Either delete the PVCs or jobs and notify cymain
    :param delete_pvcs_only: if False then also delete jobs
    :param testrun_id:
    """
    logger.info(f'Run {testrun_id} completed')
    state = await get_build_state(testrun_id)
    if state:
        await state.notify_run_completed()
        await delete_pvcs(state)
        if not delete_pvcs_only:
            await delete_jobs(state)
        # clean up other redis state
        await db.cleanup(testrun_id)


async def handle_testrun_error(event: schemas.AgentTestRunErrorEvent):
    logger.info(f'Run {event.testrun_id} failed at stage {event.report.stage}')
    await app.httpclient.post(f'/agent/testrun/{event.testrun_id}/error', json=event.report.dict())
    await handle_run_completed(event.testrun_id)


async def delete_testrun_job(job, trid: int = None):
    logger.info(f"Deleting existing job {job.metadata.name}", trid=trid)
    await async_delete_job(job.metadata.name)
    tr = await get_testrun(trid)
    if tr:
        # just in case the test run failed and didn't clean up, do it here
        await handle_run_completed(trid)


async def delete_jobs_for_branch(testrun: schemas.NewTestRun):
    if settings.K8:
        # delete any job already running
        api = get_batch_api()
        jobs = await api.list_namespaced_job(settings.NAMESPACE,
                                             label_selector=f'project_id={testrun.project.id},branch={testrun.branch}')
        if jobs.items:
            logger.info(f'Found {len(jobs.items)} existing Jobs - deleting them')
            # delete it (there should just be one, but iterate anyway)
            for job in jobs.items:
                await delete_testrun_job(job, testrun.id)


async def delete_jobs_for_project(project_id):
    api = get_batch_api()
    jobs = await api.list_namespaced_job(settings.NAMESPACE, label_selector=f'project_id={project_id}')
    if jobs.items:
        logger.info(f'Found {len(jobs.items)} existing Jobs - deleting them')
        for job in jobs.items:
            await delete_testrun_job(job)


async def delete_pvcs(state: TestRunBuildState, cancelling=False):
    # we'll let the RW PVC be deleted by the prepare_cache
    if state.rw_build_pvc and (cancelling or state.node_snapshot_name):
        await async_delete_pvc(state.rw_build_pvc)
    if state.ro_build_pvc:
        await async_delete_pvc(state.ro_build_pvc)


async def delete_jobs(state: TestRunBuildState):
    if state.preprovision_job:
        await async_delete_job(state.preprovision_job)
    if state.build_job:
        await async_delete_job(state.build_job)
    if state.run_job:
        await async_delete_job(state.run_job)


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


async def handle_delete_project(project_id: int, organisation_id: int):
    logger.info(f'Deleting project {project_id}')
    r = async_redis()
    async for key in r.scan_iter('testrun:state:*'):
        st = await r.get(key)
        if st:
            buildstate: TestRunBuildState = TestRunBuildState.parse_raw(st)
            if buildstate.project_id == project_id:
                await delete_jobs(buildstate)
                await delete_pvcs(buildstate, True)
                await r.delete(key)

    await cache.clear_cache(organisation_id)
