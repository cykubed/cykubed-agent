from datetime import datetime
from functools import cache

import pymongo
from motor.motor_asyncio import AsyncIOMotorClient

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
    await specfile_coll().insert_many([{'trid': testrun_id, 'file': f, 'started': None, 'finished': None} for f in details.specs])
    await runs_coll().find_one_and_update({'id': testrun_id}, {'$set': {'status': 'running',
                                                                        'cache_key': details.cache_hash}})


async def delete_testrun(trid: int):
    await specfile_coll().delete({'trid': trid})
    await runs_coll().delete({'id': trid})


async def delete_project(project_id: int):
    trids = [doc['id'] for doc in await runs_coll().find({'project.id': project_id}, ['id'])]
    await specfile_coll().delete({'trid': {'$in': trids}})


async def get_testrun(testrun_id: int):
    return await runs_coll().find_one({'id': testrun_id})


async def assign_next_spec(testrun_id: int, pod_name: str = None) -> str | None:
    toset = {'started': datetime.utcnow()}
    if pod_name:
        toset['pod_name'] = pod_name
    s = await specfile_coll().find_one_and_update({'trid': testrun_id,
                                                   'started': None},
                                                   {'$set': toset})
    if s:
        return s['file']


async def spec_completed(trid: int, file: str):
    await specfile_coll().find_one_and_update({'trid': trid, 'file': file},
                                              {'$set': {'finished': datetime.utcnow()}})
