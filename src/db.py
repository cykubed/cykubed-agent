import datetime

from common.redisutils import async_redis
from common.schemas import NewTestRun, CacheItem, AgentTestRun
from common.utils import utcnow
from settings import settings


#
# Odd bit of redirection is purely to make mocking easier
#


async def new_testrun(tr: NewTestRun):
    await async_redis().set(f'testrun:{tr.id}', tr.json())
    await async_redis().sadd('testruns', str(tr.id))


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
    await async_redis().set(f'testrun:{id}', item.json())


async def get_cached_item(key: str) -> CacheItem | None:
    r = async_redis()
    exists = await r.sismember('builds', key)
    if not exists:
        return None

    # update expiry
    item = CacheItem.parse_raw(await async_redis().get(f'build:{key}'))
    item.expires = utcnow() + datetime.timedelta(seconds=item.ttl)
    await async_redis().set(f'build:{key}', item.json())
    return item


async def add_cached_item(key: str, ttl: int):
    await async_redis().sadd('builds', key)
    item = CacheItem(name=key,
                     ttl=ttl,
                     expires=utcnow() + datetime.timedelta(seconds=settings.APP_DISTRIBUTION_CACHE_TTL))
    await async_redis().set(f'build:{key}', item.json())


async def add_node_cache_item(tr: AgentTestRun):
    await add_cached_item(f'node-{tr.cache_key}', settings.NODE_DISTRIBUTION_CACHE_TTL)


async def add_build_cache_item(tr: AgentTestRun):
    await add_cached_item(f"build-{tr.sha}-ro", settings.APP_DISTRIBUTION_CACHE_TTL)
