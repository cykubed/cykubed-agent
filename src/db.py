import datetime

from common.redisutils import async_redis
from common.schemas import NewTestRun, CacheItem
from common.utils import utcnow
from settings import settings


#
# Odd bit of redirection is purely to make mocking easier
#


async def new_testrun(tr: NewTestRun):
    r = async_redis()
    await r.set(f'testrun:{tr.id}', tr.json(), ex=24*3600)
    await r.sadd('testruns', str(tr.id))
    await r.set(f'testrun:{tr.id}:run_duration', 0, ex=24*3600)


async def get_testrun(id: int) -> NewTestRun | None:
    """
    Used by agents and runners to return a deserialised NewTestRun
    :param id:
    :return:
    """
    d = await async_redis().get(f'testrun:{id}')
    if d:
        return NewTestRun.parse_raw(d)
    return None


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


async def add_cached_item(key: str, ttl=settings.NODE_DISTRIBUTION_CACHE_TTL,
                          **kwargs) -> CacheItem:
    item = CacheItem(name=key,
                     ttl=ttl,
                     expires=utcnow() + datetime.timedelta(seconds=ttl), **kwargs)
    await async_redis().set(f'cache:{key}', item.json())
    return item


async def add_build_snapshot_cache_item(sha: str, node_snapshot_name: str, specs: list[str]) -> CacheItem:
    return await add_cached_item(f'build-{sha}', ttl=settings.APP_DISTRIBUTION_CACHE_TTL,
                                 node_snapshot=node_snapshot_name, specs=specs)


async def get_build_snapshot_cache_item(sha: str) -> CacheItem:
    return await get_cached_item(f'build-{sha}')


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


