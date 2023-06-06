import asyncio

from kubernetes_asyncio.client import ApiException, V1JobStatus
from loguru import logger

from common.exceptions import BuildFailedException
from settings import settings
from src.common.k8common import get_batch_api, get_custom_api, get_core_api


async def async_get_pvc(pvc_name: str) -> bool:
    # check if the PVC exists
    try:
        return await get_core_api().read_namespaced_persistent_volume_claim(pvc_name, settings.NAMESPACE)
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


def create_snapshot(yamlobjects):
    get_custom_api().create_namespaced_custom_object(group="snapshot.storage.k8s.io",
                                                     version="v1",
                                                     namespace=settings.NAMESPACE,
                                                     plural="volumesnapshots",
                                                     body=yamlobjects)


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


async def async_get_job_status(name: str) -> V1JobStatus:
    api = get_batch_api()
    try:
        job = await api.read_namespaced_job_status(name=name, namespace=settings.NAMESPACE)
        return job.status
    except ApiException as ex:
        if ex.status != 404:
            logger.exception('Failed to fetch job status')
        return None


#
# Async
#



async def async_delete_pvc(name: str):
    try:
        await get_core_api().delete_namespaced_persistent_volume_claim(name, settings.NAMESPACE)
    except ApiException as ex:
        if ex.status != 404:
            logger.exception('Failed to delete PVC')


async def async_get_snapshot(name: str):
    return await asyncio.to_thread(get_snapshot, name)


async def async_delete_snapshot(name: str):
    return await asyncio.to_thread(delete_snapshot, name)


async def async_delete_job(name: str):
    try:
        await get_batch_api().delete_namespaced_job(name, settings.NAMESPACE,
                                                    propagation_policy='Background')
    except ApiException as ex:
        if ex.status == 404:
            return
        else:
            logger.error(f'Failed to delete job {name}')


async def async_create_snapshot(yamlobjects):
    return await asyncio.to_thread(create_snapshot, yamlobjects)
