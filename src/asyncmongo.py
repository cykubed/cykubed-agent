from datetime import timedelta
from functools import cache

import aiofiles
import pymongo
from motor.motor_asyncio import AsyncIOMotorClient

from cache import get_app_distro_filename
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
        return AsyncIOMotorClient(host='cykube-mongodb-0.cykube-mongodb-headless',
                                  username='root',
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


async def init_indexes():
    await runs_coll().create_index([("id", pymongo.ASCENDING)])
    await specs_coll().create_index([("trid", pymongo.ASCENDING), ("started", pymongo.ASCENDING)])


async def new_run(tr: NewTestRun):
    trdict = tr.dict()
    trdict['started'] = utcnow()
    await runs_coll().insert_one(trdict)


async def cancel_testrun(trid: int):
    await specs_coll().delete_many({'trid': trid})
    await runs_coll().update_one({'id': trid}, {'$set': {'status': 'cancelled'}})


async def delete_project(project_id: int):
    trs = []
    async for doc in runs_coll().find({'project.id': project_id}):
        trs.append(doc)
    await specs_coll().delete_one({'trid': {'$in': [x['id'] for x in trs]}})
    for tr in trs:
        path = get_app_distro_filename(tr)
        if await aiofiles.os.path.exists(path):
            await aiofiles.os.remove(path)
        await runs_coll().delete_one({'_id': tr['_id']})


async def get_stale_testruns():
    testruns = []
    dt = utcnow() - timedelta(seconds=settings.APP_DISTRIBUTION_CACHE_TTL)
    async for doc in runs_coll().find({'finished': {'$ne': None}, 'started': {'$lt': dt}}):
        testruns.append(doc)
    return testruns


async def remove_testruns(ids):
    await specs_coll().delete_many({'trid': {'$in': ids}})
    await runs_coll().delete_many({'id': {'$in': ids}})

