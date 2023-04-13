from common.redisutils import async_redis
from common.schemas import NewTestRun


#
# Odd bit of redirection is purely to make mocking easier
#


async def new_testrun(tr: NewTestRun):
    await async_redis().set(f'testrun:{tr.id}', tr.json())


async def cancel_testrun(trid: int):
    """
    Just remove the keys
    :param trid: test run ID
    """
    r = async_redis()
    await r.delete(f'testrun:{trid}:specs')
    await r.delete(f'testrun:{trid}')


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
