import os
from datetime import datetime
from functools import cache

import aiofiles
import pymongo
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument

from common.enums import INACTIVE_STATES, TestRunStatus
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


async def set_status(trid: int, status: TestRunStatus):
    if status in INACTIVE_STATES:
        # remove all the specs and delete the run
        await specfile_coll().delete_many({'trid': trid})
        await runs_coll().delete_one({'id': trid})
    else:
        await runs_coll().find_one_and_update({'id': trid}, {'$set': {'status': status}})


async def set_build_details(testrun_id: int, details: CompletedBuild) -> NewTestRun:
    await specfile_coll().insert_many([{'trid': testrun_id, 'file': f, 'started': None, 'finished': None} for f in details.specs])
    tr = await runs_coll().find_one_and_update({'id': testrun_id}, {'$set': {'status': 'running',
                                                                        'cache_key': details.cache_hash}},
                                               return_document = ReturnDocument.AFTER)
    return NewTestRun.parse_obj(tr)


async def delete_testrun(trid: int):
    await specfile_coll().delete({'trid': trid})
    await runs_coll().delete({'id': trid})


async def delete_project(project_id: int):
    trids = []
    async for doc in runs_coll().find({'project.id': project_id}, ['id']):
        trids.append(doc['id'])
    await specfile_coll().delete({'trid': {'$in': trids}})


async def get_testrun(testrun_id: int):
    return await runs_coll().find_one({'id': testrun_id})


async def get_inactive_testrun_ids():
    trids = []
    async for doc in runs_coll().find({'status': {'$in': INACTIVE_STATES}}, ['id']):
        trids.append(doc['id'])

    return trids


async def remove_testruns(ids):
    await specfile_coll().delete_many({'trid': {'$in': ids}})
    await runs_coll().delete_many({id: {'$in': ids}})


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
    """
    Remove the spec
    :param trid:
    :param file:
    :return:
    """
    await specfile_coll().delete_one({'trid': trid, 'file': file})
    cnt = await specfile_coll().count_documents({'trid': trid})
    if not cnt:
        path = os.path.join(settings.CYKUBE_CACHE_DIR, f'{trid}.tar.lz4')
        if os.path.exists(path):
            await aiofiles.os.remove(path)
        await runs_coll().delete_one({'id': trid})
