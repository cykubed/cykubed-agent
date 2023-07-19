from common.redisutils import async_redis
from common.schemas import NewTestRun


#
# Odd bit of redirection is purely to make mocking easier
#


async def new_testrun(tr: NewTestRun):
    r = async_redis()
    await r.set(f'testrun:{tr.id}', tr.json())


async def set_specs(tr: NewTestRun, specs: list[str]):
    await async_redis().sadd(f'testrun:{tr.id}:specs', *specs)
    await async_redis().set(f'testrun:{tr.id}:to-complete', len(specs))


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


