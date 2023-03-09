from datetime import timedelta
from functools import cache

import pymongo
from loguru import logger
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorGridFSBucket

from common.schemas import NewTestRun
from common.settings import settings
from common.utils import utcnow


@cache
def async_client():
    if settings.TEST:
        from mongomock_motor import AsyncMongoMockClient
        return AsyncMongoMockClient()

    if settings.MONGO_ROOT_PASSWORD:
        # in-cluster
        return AsyncIOMotorClient(host=settings.MONGO_HOST.split(','),
                                  username=settings.MONGO_USER,
                                  password=settings.MONGO_ROOT_PASSWORD)
    return AsyncIOMotorClient()


@cache
def async_db():
    return async_client()[settings.MONGO_DATABASE]


@cache
def runs_coll():
    return async_db()['run']


@cache
def specs_coll():
    return async_db()['spec']


@cache
def messages_coll():
    return async_db()['message']


@cache
def fs():
    return AsyncIOMotorGridFSBucket(async_db())


@cache
def fsfetched_coll():
    """
    The fetched collection stores a timestamp each time a GridFS-cached item is fetched
    """
    return async_db()['fetched']


async def init_indexes():
    await runs_coll().create_index([("id", pymongo.ASCENDING)])
    await runs_coll().create_index([("started", pymongo.ASCENDING)])
    await specs_coll().create_index([("trid", pymongo.ASCENDING), ("started", pymongo.ASCENDING)])
    await fsfetched_coll().create_index([("filename", pymongo.ASCENDING)])
    await fsfetched_coll().create_index([("ts", pymongo.ASCENDING)])


async def new_run(tr: NewTestRun):
    trdict = tr.dict()
    trdict['started'] = utcnow()
    await runs_coll().insert_one(trdict)


async def cancel_testrun(trid: int):
    await specs_coll().delete_many({'trid': trid})
    await runs_coll().update_one({'id': trid}, {'$set': {'status': 'cancelled'}})


async def remove_testruns(ids):
    await specs_coll().delete_many({'trid': {'$in': ids}})
    await runs_coll().delete_many({'id': {'$in': ids}})


async def cleanup_cache():
    # first get stale runs
    testruns = []
    dt = utcnow() - timedelta(seconds=settings.APP_DISTRIBUTION_CACHE_TTL)
    async for testrundoc in runs_coll().find({'finished': {'$ne': None}, 'started': {'$lt': dt}}):
        logger.info(f"Deleting testrun {testrundoc['id']}")
        testruns.append(testrundoc)
        # clear the distro if there is one
        async for fsdoc in fs().find({'filename': testrundoc['sha']}):
            await fs().delete(fsdoc._id)

    trids = [x['id'] for x in testruns]
    await specs_coll().delete_many({'trid': {'$in': trids}})
    await runs_coll().delete_many({'id': {'$in': trids}})

    # now clear node caches that haven't been used in a while
    dt = utcnow() - timedelta(seconds=settings.NODE_DISTRIBUTION_CACHE_TTL)
    async for doc in fsfetched_coll().find({'ts': {'$lt': dt}}):
        logger.info(f"Deleting node cache {doc.name}")
        await fsfetched_coll().delete_one({'filename': doc.name})
        await fs().delete(doc._id)





