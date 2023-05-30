import asyncio

from kubernetes import client
from kubernetes.client import ApiException, V1JobStatus
from loguru import logger

from common.exceptions import BuildFailedException
from common.k8common import get_core_api, get_custom_api, get_batch_api
from settings import settings


def delete_pvc(name: str):
    try:
        get_core_api().delete_namespaced_persistent_volume_claim(name, settings.NAMESPACE)
    except ApiException as ex:
        if ex.status != 404:
            logger.exception('Failed to delete PVC')


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
        logger.debug(f'Delete snapshot {name}')
        get_custom_api().delete_namespaced_custom_object(group="snapshot.storage.k8s.io",
                                                    version="v1beta1",
                                                    namespace=settings.NAMESPACE,
                                                    plural="volumesnapshots",
                                                    name=name)
    except ApiException as ex:
        if ex.status == 404:
            # already deleted - ignore
            logger.debug(f'Snapshot {name} cannot be deleted as it does not exist')
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


def delete_job(name: str):
    try:
        logger.info(f"Deleting existing job {name}")
        client.BatchV1Api().delete_namespaced_job(name, settings.NAMESPACE,
                                                  propagation_policy='Background')
    except ApiException as ex:
        if ex.status == 404:
            return
        else:
            logger.error(f'Failed to delete job {name}')


def get_job_status(name: str) -> V1JobStatus:
    api = get_batch_api()
    try:
        job = api.read_namespaced_job_status(name=name, namespace=settings.NAMESPACE)
        return job.status
    except ApiException as ex:
        if ex.status != 404:
            logger.exception('Failed to fetch job status')
        return False


def is_pod_running(podname: str):
    v1 = client.CoreV1Api()
    try:
        v1.read_namespaced_pod(podname, settings.NAMESPACE)
        return True
    except ApiException:
        return False

#
# Async
#


async def async_get_snapshot(name: str):
    return await asyncio.to_thread(get_snapshot, name)


async def async_delete_pvc(name: str):
    await asyncio.to_thread(delete_pvc, name)


async def async_delete_snapshot(name: str):
    await asyncio.to_thread(delete_snapshot, name)


async def async_delete_job(name: str):
    if name:
        await asyncio.to_thread(delete_job, name)


async def async_get_job_status(name: str) -> V1JobStatus:
    if name:
        return await asyncio.to_thread(get_job_status, name)
    return None


async def async_get_pvc(name: str):
    return await asyncio.to_thread(get_pvc, name)


async def async_is_pod_running(podname: str):
    return await asyncio.to_thread(is_pod_running, podname)
