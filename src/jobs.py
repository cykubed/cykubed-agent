import asyncio
import os

import aiofiles
import chevron
import yaml
from chevron import ChevronError
from kubernetes import client, utils as k8utils
from kubernetes.client import ApiException
from loguru import logger
from yaml import YAMLError

from app import app
from common import schemas
from common.exceptions import InvalidTemplateException, BuildFailedException
from common.k8common import NAMESPACE, get_core_api, get_custom_api
from common.schemas import CacheItemType, CacheItem
from db import get_testrun, save_testrun, expired_cached_items_iter, get_cached_item, get_build_ro_pvc_name, \
    remove_cached_item, get_build_pvc_name, add_cached_item, get_node_snapshot_name, get_node_pvc_name, \
    get_node_ro_pvc_name
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
                project_id=testrun.project.id,
                local_id=testrun.local_id,
                testrun_id=testrun.id,
                testrun=testrun,
                branch=testrun.branch,
                runner_image=testrun.project.runner_image,
                timezone=testrun.project.timezone,
                token=settings.API_TOKEN,
                cpu_request=testrun.project.runner_cpu,
                cpu_limit=testrun.project.runner_cpu,
                gke_spot_enabled=testrun.project.spot_enabled,
                gke_spot_percentage=testrun.project.spot_percentage,
                deadline=testrun.project.runner_deadline,
                memory_request=testrun.project.runner_memory,
                memory_limit=testrun.project.runner_memory,
                storage=testrun.project.runner_ephemeral_storage)


async def create_k8_objects(jobtype, context):
    try:
        template = await get_job_template(jobtype)
        jobyaml = chevron.render(template, context)
        yamlobjects = list(yaml.safe_load_all(jobyaml))
        k8sclient = client.ApiClient()
        # should only be one object
        kind = yamlobjects[0]['kind']
        name = yamlobjects[0]['metadata']['name']
        k8utils.create_from_yaml(k8sclient, yaml_objects=yamlobjects, namespace=NAMESPACE)
        logger.info(f'Created {kind} {name}', id=context['testrun_id'])
    except YAMLError as ex:
        raise InvalidTemplateException(f'Invalid YAML in {jobtype} template: {ex}')
    except ChevronError as ex:
        raise InvalidTemplateException(f'Invalid {jobtype} template: {ex}')


async def check_cached(key: str, update_expiry=True) -> CacheItem | None:
    item = await get_cached_item(key, update_expiry)
    if item:
        if item.type == CacheItemType.pvc:
            if await async_check_pvc_exists(item.name):
                return item
            # nope - someone else must have deleted it
            await remove_cached_item(key)
        else:
            # snapshot
            if await async_check_snapshot_exists(item.name):
                return item
            # nope - clean up
            await remove_cached_item(key)


async def create_clone_job(testrun: schemas.AgentTestRun):
    """
    If there is already a built distribution PVC then go straight to creating the runners
    Otherwise create a build PVC and kick off the clone job
    :param testrun:
    :return:
    """
    # stop existing jobs
    delete_jobs_for_branch(testrun.id, testrun.branch)

    # check for an existing ro build pvc for this sha
    ro_build_pvc_name = get_build_ro_pvc_name(testrun)
    build = not bool(await check_cached(ro_build_pvc_name))
    if not build:
        # we've already got a RO build PVC - we'll need a node dist
        node_snapshot_name = get_node_snapshot_name(testrun)
        if not await check_cached(node_snapshot_name):
            # we shouldn't get here, so delete the build PVC and let's rebuild
            await async_delete_pvc(ro_build_pvc_name)
            build = True
        else:
            await app.update_status(testrun.id, 'running')
            await create_runner_job(testrun)

    if build:
        context = common_context(testrun)
        # do we have a build PVC (i.e i.e the previous build may have failed)
        pvc_name = get_build_pvc_name(testrun)
        context['build_pvc_name'] = pvc_name
        if not await check_cached(pvc_name):
            # no build PVC - create it
            context['pvc_name'] = pvc_name
            await create_k8_objects('rw-pvc', context)
            await add_cached_item(pvc_name, CacheItemType.pvc)
        # and clone
        await create_k8_objects('clone', context)
        await app.update_status(testrun.id, 'building')


async def create_ro_node_pvc_from_snapshot(testrun: schemas.AgentTestRun):
    node_snapshot_name = get_node_snapshot_name(testrun)
    context = common_context(testrun)
    context['snapshot_name'] = node_snapshot_name
    context['ro_pvc_name'] = get_node_ro_pvc_name(testrun)
    await create_k8_objects('ro-pvc-from-snapshot', context)


async def handle_clone_completed(testrun_id: int):
    """
    Check for a volume snapshot of the node dist (which will include the Cypress cache).
    If it exists then it be
    :param testrun_id:
    :return:
    """
    testrun = await get_testrun(testrun_id)
    context = common_context(testrun)
    context.update(dict(node_cache_key=testrun.cache_key,
                        job_name=f'cykubed-build-{testrun.project.name}-{testrun.id}',
                        build_pvc_name=get_build_pvc_name(testrun)))
    # check for node snapshot
    node_snapshot_name = get_node_snapshot_name(testrun)
    if await check_cached(node_snapshot_name):
        # we have a cached node distribution (i.e a VolumeSnapshot for it) - create a read-only PVC
        await create_ro_node_pvc_from_snapshot(testrun)
        testrun.node_cache_hit = True
        node_pvc_name = context['node_pvc_name'] = get_node_ro_pvc_name(testrun)
        context['node_snapshot_name'] = node_snapshot_name
        await save_testrun(testrun)
    else:
        # otherwise this will need to build the node dist: create a RW pvc and
        node_pvc_name = context['node_pvc_name'] = context['pvc_name'] = get_node_pvc_name(testrun)
        if not await async_check_pvc_exists(node_pvc_name):
            # it shouldn't, but just in case
            await create_k8_objects('rw-pvc', context)

    # record it so we can garbage collect if it all goes wrong
    await add_cached_item(node_pvc_name, CacheItemType.pvc)

    # now create the build
    await create_k8_objects('build', context)


async def build_completed(testrun_id: int):
    testrun = await get_testrun(testrun_id)
    context = common_context(testrun)
    node_ro_pvc_name = context['ro_pvc_name'] = get_node_ro_pvc_name(testrun)
    snapshot_name = context['snapshot_name'] = get_node_snapshot_name(testrun)
    if not testrun.node_cache_hit:
        # take a snapshot of the node dist
        context['rw_pvc_name'] = context['pvc_name'] = get_node_pvc_name(testrun)
        await create_k8_objects('pvc-snapshot', context)
        await add_cached_item(snapshot_name, CacheItemType.snapshot)
        # create a RO PVC from this RW PVC
        await create_k8_objects('ro-pvc-from-pvc', context)
        # and delete the RW node PVC
        await delete_cached_pvc(context['rw_pvc_name'])
    else:
        # create a many-read-only volume from the snapshot
        await create_k8_objects('ro-pvc-from-snapshot', context)
        await add_cached_item(node_ro_pvc_name, CacheItemType.pvc)

    # next create the runner job
    await create_runner_job(testrun)


async def create_runner_job(testrun: schemas.AgentTestRun):
    context = common_context(testrun)
    context['name'] = f'cykubed-runner-{testrun.project.name}-{testrun.id}'
    parallelism = min(testrun.project.parallelism, len(testrun.specs))
    context.update(dict(parallelism=parallelism))
    context['build_pvc_name'] = get_build_ro_pvc_name(testrun)
    context['node_pvc_name'] = get_node_ro_pvc_name(testrun)
    await create_k8_objects('runner', context)


async def handle_run_completed(testrun_id):
    """
    Just delete the node cache - keep the build PVC around in case of reruns,
    and let the pruner delete it
    :param testrun_id:
    """
    testrun = await get_testrun(testrun_id)
    # delete the PVCs
    await delete_cached_pvc(get_node_ro_pvc_name(testrun))
    await delete_cached_pvc(get_build_ro_pvc_name(testrun))


async def prune_cache_loop():
    """
    Pune expired snapshots and PVCs
    :return:
    """
    while app.is_running():
        await asyncio.sleep(300)
        await prune_cache()


async def prune_cache():
    async for item in expired_cached_items_iter():
        if item.type == CacheItemType.snapshot:
            # delete volume
            await async_delete_snapshot(item.name)
        else:
            # delete pvc
            await async_delete_pvc(item.name)


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


async def async_check_snapshot_exists(name: str) -> bool:
    details = await asyncio.to_thread(get_snapshot, name)
    return bool(details)


async def async_delete_pvc(name: str):
    await asyncio.to_thread(delete_pvc, name)


async def async_delete_snapshot(name: str) -> bool:
    details = await asyncio.to_thread(delete_snapshot, name)
    return bool(details)


async def async_check_pvc_exists(name: str) -> bool:
    details = await asyncio.to_thread(get_pvc, name)
    return bool(details)


def delete_job(job, trid: int = None):
    logger.info(f"Deleting existing job {job.metadata.name}", trid=trid)
    client.BatchV1Api().delete_namespaced_job(job.metadata.name, NAMESPACE)
    poditems = get_core_api().list_namespaced_pod(NAMESPACE,
                                                  label_selector=f"job-name={job.metadata.name}").items
    if poditems:
        for pod in poditems:
            logger.info(f'Deleting pod {pod.metadata.name}', id=trid)
            get_core_api().delete_namespaced_pod(pod.metadata.name, NAMESPACE)


def delete_jobs_for_branch(trid: int, branch: str):
    # delete any job already running
    api = client.BatchV1Api()
    jobs = api.list_namespaced_job(NAMESPACE, label_selector=f'branch={branch}')
    if jobs.items:
        logger.info(f'Found {len(jobs.items)} existing Jobs - deleting them', trid=trid)
        # delete it (there should just be one, but iterate anyway)
        for job in jobs.items:
            delete_job(job, trid)


def delete_jobs_for_project(project_id):
    api = client.BatchV1Api()
    jobs = api.list_namespaced_job(NAMESPACE, label_selector=f'project_id={project_id}')
    if jobs.items:
        logger.info(f'Found {len(jobs.items)} existing Jobs - deleting them')
        for job in jobs.items:
            delete_job(job)


def delete_jobs(testrun_id: int):
    logger.info(f"Deleting jobs for testrun {testrun_id}")
    api = client.BatchV1Api()
    jobs = api.list_namespaced_job(NAMESPACE, label_selector=f"testrun_id={testrun_id}")
    for job in jobs.items:
        delete_job(job, testrun_id)


def is_pod_running(podname: str):
    v1 = client.CoreV1Api()
    try:
        v1.read_namespaced_pod(podname, NAMESPACE)
        return True
    except ApiException:
        return False


