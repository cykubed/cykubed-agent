import asyncio
import os

import aiofiles
import chevron
import yaml

from chevron import ChevronError
from kubernetes_asyncio import watch, utils as k8utils
from kubernetes_asyncio.client import ApiException, V1JobStatus
from loguru import logger
from yaml import YAMLError

from common.exceptions import BuildFailedException, InvalidTemplateException
from common.k8common import get_batch_api, get_custom_api, get_core_api, get_client
from settings import settings


async def async_get_pvc(pvc_name: str) -> bool:
    # check if the PVC exists
    try:
        return await get_core_api().read_namespaced_persistent_volume_claim(pvc_name, settings.NAMESPACE)
    except ApiException as ex:
        if ex.status == 404:
            return False
        else:
            raise BuildFailedException('Failed to determine existence of build PVC')


async def async_delete_snapshot(name: str):
    try:
        logger.debug(f'Delete snapshot {name}')
        await get_custom_api().delete_namespaced_custom_object(group="snapshot.storage.k8s.io",
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


async def async_create_snapshot(yamlobjects):
    await get_custom_api().create_namespaced_custom_object(group="snapshot.storage.k8s.io",
                                                     version="v1",
                                                     namespace=settings.NAMESPACE,
                                                     plural="volumesnapshots",
                                                     body=yamlobjects)


async def async_get_snapshot(name: str):
    try:
        return await get_custom_api().get_namespaced_custom_object(group="snapshot.storage.k8s.io",
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


async def async_delete_job(name: str):
    try:
        await get_batch_api().delete_namespaced_job(name, settings.NAMESPACE,
                                                    propagation_policy='Background')
    except ApiException as ex:
        if ex.status == 404:
            return
        else:
            logger.error(f'Failed to delete job {name}')


async def wait_for_pvc_ready(pvc_name: str):
    v1 = get_core_api()
    async with watch.Watch().stream(v1.list_namespaced_persistent_volume_claim,
                                    field_selector=f"metadata.name={pvc_name}",
                                    namespace=settings.NAMESPACE, timeout_seconds=10) as stream:
        async for event in stream:
            pvcobj = event['object']
            if pvcobj.status.phase == 'Bound':
                logger.debug(f'PVC {pvc_name} is bound')
                return


async def create_from_dict(data: dict):
    await k8utils.create_from_dict(get_client(),
                                   data,
                                   namespace=settings.NAMESPACE)


async def create_k8_objects(jobtype, context) -> str:
    try:
        yamlobjects = await render_template(jobtype, context)
        # should only be one object
        kind = yamlobjects[0]['kind']
        name = yamlobjects[0]['metadata']['name']
        logger.info(f'Creating {kind} {name}', id=context['testrun_id'])
        await create_from_dict(yamlobjects[0])
        return name
    except YAMLError as ex:
        raise InvalidTemplateException(f'Invalid YAML in {jobtype} template: {ex}')
    except ChevronError as ex:
        raise InvalidTemplateException(f'Invalid {jobtype} template: {ex}')
    except Exception as ex:
        logger.exception(f"Failed to create {jobtype}")
        raise ex


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


async def get_job_template(name: str) -> str:
    async with aiofiles.open(get_template_path(name), mode='r') as f:
        return await f.read()


def get_template_path(name: str) -> str:
    return os.path.join(TEMPLATES_DIR, f'{name}.mustache')


TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), 'k8config', 'templates')
