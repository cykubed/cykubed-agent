import datetime

from loguru import logger

from common.redisutils import async_redis
from common.schemas import NewTestRun, CacheItem, AgentTestRun, CacheItemType
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


async def cancel_testrun(trid: int):
    """
    Just remove the keys
    :param trid: test run ID
    """
    r = async_redis()
    await r.delete(f'testrun:{trid}:specs')
    await r.delete(f'testrun:{trid}')
    await r.srem(f'testruns', str(trid))


async def get_testrun(id: int) -> AgentTestRun | None:
    """
    Used by agents and runners to return a deserialised AgentTestRun
    :param id:
    :return:
    """
    d = await async_redis().get(f'testrun:{id}')
    if d:
        return AgentTestRun.parse_raw(d)
    return None


async def save_testrun(item: AgentTestRun):
    logger.debug(f'Save testrun {item.json()}')
    await async_redis().set(f'testrun:{id}', item.json())


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


async def add_cached_item(key: str, itemtype: CacheItemType) -> CacheItem:
    ttl = settings.NODE_DISTRIBUTION_CACHE_TTL if itemtype == CacheItemType.snapshot \
        else settings.APP_DISTRIBUTION_CACHE_TTL
    item = CacheItem(name=key,
                     ttl=ttl,
                     type=itemtype,
                     expires=utcnow() + datetime.timedelta(seconds=ttl))
    await async_redis().set(f'cache:{key}', item.json())
    return item


async def remove_cached_item(key: str):
    await async_redis().delete(f'cache:{key}')


def get_build_ro_pvc_name(tr: AgentTestRun):
    return f"build-ro-pvc-{tr.sha}"


def get_build_pvc_name(tr: AgentTestRun):
    return f"build-pvc-{tr.sha}"


def get_node_snapshot_name(tr: AgentTestRun):
    return f"node-snap-{tr.cache_key}"


def get_node_pvc_name(tr: AgentTestRun):
    return f"node-pvc-{tr.cache_key}"


def get_node_ro_pvc_name(tr: AgentTestRun):
    return f"node-ro-pvc-{tr.cache_key}"


async def expired_cached_items_iter():
    async for key in async_redis().scan_iter('cache:*'):
        item = await get_cached_item(key[6:], False)
        if item.expires < utcnow():
            yield item


