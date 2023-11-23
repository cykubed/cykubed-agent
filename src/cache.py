import asyncio
import datetime

from kubernetes_asyncio.client import V1PersistentVolumeClaimList, V1JobList
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_fixed, wait_random

from app import app
from common.exceptions import ServerConnectionFailed
from common.k8common import get_core_api, get_custom_api, init, get_batch_api
from common.redisutils import async_redis
from common.schemas import CacheItem
from common.utils import utcnow
from k8utils import async_delete_snapshot, async_delete_job
from settings import settings
from state import get_build_state

cache = dict()


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
    logger.info('Running gargbage collector')
    v1 = get_core_api()
    result: V1PersistentVolumeClaimList = await v1.list_namespaced_persistent_volume_claim(settings.NAMESPACE)
    for item in result.items:
        if item.metadata.labels:
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
        item = await get_cached_item(name, False)
        if not item:
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

    async for item in cached_items_iter(organisation_id):
        await delete_cached_item(item)


async def prune_cache():
    async for item in expired_cached_items_iter():
        await delete_cached_item(item)


async def garage_collect_loop():
    """
    Garbage collect on startup and every hour (although this should be a NOP)
    """
    while app.is_running():
        await garbage_collect_cache()
        await asyncio.sleep(3600)


async def prune_cache_loop():
    """
    Prune expired snapshots and PVCs
    :return:
    """
    while app.is_running():
        await prune_cache()
        await asyncio.sleep(300)


@retry(stop=stop_after_attempt(settings.MAX_HTTP_RETRIES if not settings.TEST else 1),
       wait=wait_fixed(5) + wait_random(0, 4))
async def fetch_cached_items():
    r = await app.httpclient.get('/agent/cached-items')
    if r.status_code != 200:
        logger.error('Failed to fetch cached-items!')
        raise ServerConnectionFailed()

    for item in r.json():
        cache[item.name] = item


async def delete_cached_item(item: CacheItem):
    # delete volume
    await async_delete_snapshot(item.name)
    if settings.LOCAL_REDIS:
        await async_redis().delete(f'cache:{item.name}')
    else:
        r = await app.httpclient.delete(f'/agent/cached-item/{item.name}')
        if r.status_code != 200:
            logger.error(f'Failed to tell server about deleted cache item ({r.status_code})')


async def get_cached_item(key: str, update_expiry=True) -> CacheItem | None:
    if settings.LOCAL_REDIS:
        itemstr = await async_redis().get(f'cache:{key}')
        if not itemstr:
            return None
        item = CacheItem.parse_raw(itemstr)
    else:
        item = cache.get(key)
        if not item:
            return None

    if update_expiry:
        # update expiry
        item.expires = utcnow() + datetime.timedelta(seconds=item.ttl)
        if settings.LOCAL_REDIS:
            await async_redis().set(f'cache:{key}', item.json())
        else:
            # tell the server, then update the local copy
            r = await app.httpclient.put('/agent/cached-item', json=item.json())
            if r.status_code != 200:
                logger.error(f"Failed to update cache-item {item.name}: {r.status_code}")

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
    if settings.LOCAL_REDIS:
        await async_redis().set(f'cache:{key}', item.json())
    else:
        cache[key] = item
        # persist in the API
        await app.httpclient.post('/agent/cached-item', json=item.json())
    return item


async def add_build_snapshot_cache_item(organisation_id: int,
                                        sha: str, specs: list[str],
                                        storage_size: int) -> CacheItem:
    return await add_cached_item(organisation_id,
                                 f'{organisation_id}-build-{sha}',
                                 ttl=settings.APP_DISTRIBUTION_CACHE_TTL,
                                 specs=specs,
                                 storage_size=storage_size)


async def remove_cached_item(item: CacheItem):
    if settings.LOCAL_REDIS:
        await async_redis().delete(f'cache:{item.name}')
    else:
        pass


async def expired_cached_items_iter():
    async for item in cached_items_iter():
        if item.expires < utcnow():
            yield item


async def cached_items_iter(organisation_id: int = None):
    if settings.LOCAL_REDIS:
        async for key in async_redis().scan_iter('cache:*'):
            item = await get_cached_item(key[6:], False)
            if not item.organisation_id or item.organisation_id == organisation_id:
                yield item
    else:
        for item in cache.values():
            if not item.organisation_id or item.organisation_id == organisation_id:
                yield item
