import asyncio
import datetime

from loguru import logger

from app import app
from common.redisutils import async_redis
from common.schemas import CacheItem
from common.utils import utcnow
from k8utils import async_delete_snapshot, async_delete_pvc
from settings import settings


async def clear_cache():
    logger.info('Clearing cache')
    async for key in async_redis().scan_iter('cache:*'):
        item = await get_cached_item(key[6:], False)
        await delete_cache_item(item)


async def prune_cache():
    async for item in expired_cached_items_iter():
        await delete_cache_item(item)


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


async def add_cached_item(key: str,
                          storage_size: int,
                          ttl=settings.NODE_DISTRIBUTION_CACHE_TTL,
                          **kwargs) -> CacheItem:
    item = CacheItem(name=key,
                     ttl=ttl,
                     storage_size=storage_size,
                     expires=utcnow() + datetime.timedelta(seconds=ttl), **kwargs)
    await async_redis().set(f'cache:{key}', item.json())
    return item


async def add_build_snapshot_cache_item(sha: str, node_snapshot_name: str, specs: list[str],
                                        storage_size: int) -> CacheItem:
    return await add_cached_item(f'build-{sha}', ttl=settings.APP_DISTRIBUTION_CACHE_TTL,
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
