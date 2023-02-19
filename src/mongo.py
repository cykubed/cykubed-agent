from functools import cache

import pymongo
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument
from sqlalchemy.sql.functions import now

from common.schemas import NewTestRun, CompletedBuild
from common.settings import settings


@cache
def client():
    if settings.TEST:
        from mongomock_motor import AsyncMongoMockClient
        return AsyncMongoMockClient()
    return AsyncIOMotorClient(settings.MONGO_URL)


@cache
def db():
    return client()[settings.MONGO_DATABASE]


@cache
def runs_coll():
    return db().runs


@cache
def specfile_coll():
    return db().specfiles


async def init():
    await runs_coll().create_index([("id", pymongo.ASCENDING)])
    await specfile_coll().create_index([("trid", pymongo.ASCENDING),("started", pymongo.ASCENDING)])


async def new_run(tr: NewTestRun):
    await runs_coll().insert_one(tr.dict())


async def set_build_details(testrun_id: int, details: CompletedBuild):
    coll = runs_coll()
    await coll.find_and_update({'id': testrun_id}, {'$set': {
        'sha': details.sha,
        'cache_hash': details.cache_hash
    }})
    await specfile_coll().insert_many([{'trid': testrun_id, 'file': f} for f in details.specs])


async def get_testrun(testrun_id: int) -> NewTestRun:
    return await runs_coll().find_one({'id': testrun_id})


async def assign_next_spec(pod_name: str, testrun_id: int) -> str | None:
    s = await specfile_coll().find_one_and_update({'trid': testrun_id, 'started': None},
                                                     {'$set': {'pod_name': pod_name,
                                                               'started': now()}},
                                                     return_document=ReturnDocument.AFTER)
    if s:
        return s['file']


async def spec_completed(trid: int, file: str):
    await specfile_coll().find_one_and_update({'trid': trid, 'file': file, 'finished': now()})
