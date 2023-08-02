import asyncio
import datetime

from kubernetes_asyncio.client import V1PersistentVolumeClaimList, V1JobList
from loguru import logger

from app import app
from common.k8common import get_core_api, get_custom_api, init, get_batch_api
from common.redisutils import async_redis
from common.schemas import CacheItem
from common.utils import utcnow
from k8utils import async_delete_snapshot, async_delete_pvc, async_delete_job
from settings import settings
from state import get_build_state


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


async def garbage_collect_cache():
    # check for orphan pvcs
    v1 = get_core_api()
    result: V1PersistentVolumeClaimList = await v1.list_namespaced_persistent_volume_claim(settings.NAMESPACE)
    for item in result.items:
        testrun_id = item.metadata.labels.get('testrun_id')
        if testrun_id:
            # does it exist in the cache?
            st = await get_build_state(int(testrun_id))
            if not st:
                # orphan - delete it
                name = item.metadata.name
                logger.info(f'Found orphaned PVC {name} - deleting it')
                await v1.delete_namespaced_persistent_volume_claim(name, settings.NAMESPACE)

    # check for orphan snapshots
    snapshot_resp = await get_custom_api().list_namespaced_custom_object(group="snapshot.storage.k8s.io",
                                                               version="v1beta1",
                                                               namespace=settings.NAMESPACE,
                                                               plural="volumesnapshots")
    for item in snapshot_resp['items']:
        name = item['metadata']['name']
        cache = await get_cached_item(name, False)
        if not cache:
            # unknown - delete it
            logger.info(f'Found orphaned VolumeSnapshot {name} - deleting it')
            await async_delete_snapshot(name)


async def stuff():
    await init()
    await garbage_collect_cache()


async def clear_cache(organisation_id: int = None):
    if organisation_id:
        logger.info(f'Clearing cache for org {organisation_id}')
    else:
        logger.info('Clearing cache')

    async for key in async_redis().scan_iter('cache:*'):
        item = await get_cached_item(key[6:], False)
        if not item.organisation_id or item.organisation_id == organisation_id:
            await delete_cache_item(item)


async def prune_cache():
    async for item in expired_cached_items_iter():
        await delete_cache_item(item)


async def garage_collect_loop():
    """
    Garbage collect on startup and every hour (although this should be a NOP)
    """
    while app.is_running():
        await garbage_collect_cache()
        await asyncio.sleep(3600)


async def prune_cache_loop():
    """
    Pune expired snapshots and PVCs
    :return:
    """
    while app.is_running():
        await prune_cache()
        await asyncio.sleep(300)


async def delete_cache_item(item):
    # delete volume
    await async_delete_snapshot(item.name)
    await async_redis().delete(f'cache:{item.name}')


async def delete_cached_pvc(name: str):
    await async_delete_pvc(name)
    await remove_cached_item(name)


async def get_cached_item(key: str, update_expiry=True) -> CacheItem | None:
    itemstr = await async_redis().get(f'cache:{key}')
    if not itemstr:
        return None
    item = CacheItem.parse_raw(itemstr)
    if update_expiry:
        # update expiry
        item.expires = utcnow() + datetime.timedelta(seconds=item.ttl)
        await async_redis().set(f'cache:{key}', item.json())
    return item


async def add_cached_item(organisation_id: int,
                          key: str,
                          storage_size: int,
                          ttl=settings.NODE_DISTRIBUTION_CACHE_TTL,
                          **kwargs) -> CacheItem:
    item = CacheItem(name=key,
                     ttl=ttl,
                     organisation_id=organisation_id,
                     storage_size=storage_size,
                     expires=utcnow() + datetime.timedelta(seconds=ttl), **kwargs)
    await async_redis().set(f'cache:{key}', item.json())
    return item


async def add_build_snapshot_cache_item(organisation_id: int,
                                        sha: str, specs: list[str],
                                        storage_size: int) -> CacheItem:
    return await add_cached_item(organisation_id,
                                 f'build-{sha}',
                                 ttl=settings.APP_DISTRIBUTION_CACHE_TTL,
                                 specs=specs,
                                 storage_size=storage_size)


def get_pvc_expiry_time() -> str:
    return (utcnow() + datetime.timedelta(seconds=settings.APP_DISTRIBUTION_CACHE_TTL)).isoformat()


def get_snapshot_expiry_time() -> str:
    return (utcnow() + datetime.timedelta(seconds=settings.NODE_DISTRIBUTION_CACHE_TTL)).isoformat()


async def remove_cached_item(key: str):
    await async_redis().delete(f'cache:{key}')


async def expired_cached_items_iter():
    async for key in async_redis().scan_iter('cache:*'):
        item = await get_cached_item(key[6:], False)
        if item.expires < utcnow():
            yield item
