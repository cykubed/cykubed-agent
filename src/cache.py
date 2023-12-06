from kubernetes_asyncio.client import V1PersistentVolumeClaimList, V1JobList
from loguru import logger

from app import app
from common.k8common import get_core_api, get_custom_api, get_batch_api
from common.schemas import CacheItem
from k8utils import async_delete_snapshot, async_delete_job
from settings import settings


async def delete_all_jobs():
    resp: V1JobList = await get_batch_api().list_namespaced_job(settings.NAMESPACE)
    for job in resp.items:
        logger.info(f'Deleting job {job.metadata.name}')
        await async_delete_job(job.metadata.name)


async def delete_all_pvcs():
    v1 = get_core_api()
    result: V1PersistentVolumeClaimList = await v1.list_namespaced_persistent_volume_claim(settings.NAMESPACE)
    for item in result.items:
        await v1.delete_namespaced_persistent_volume_claim(item.metadata.name, settings.NAMESPACE)


async def delete_all_volume_snapshots():
    snapshot_resp = await get_custom_api().list_namespaced_custom_object(group="snapshot.storage.k8s.io",
                                                                         version="v1beta1",
                                                                         namespace=settings.NAMESPACE,
                                                                         plural="volumesnapshots")
    for item in snapshot_resp['items']:
        name = item['metadata']['name']
        await async_delete_snapshot(name)


async def get_cached_item(key: str) -> CacheItem | None:
    # check server
    r = await app.httpclient.get(f'/agent/cached-item/{key}')
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        logger.error(f'Unexpected error code when fetching cache item: {r.status_code}')
        return None
    return CacheItem.parse_raw(r.text)
