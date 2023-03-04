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
def archive_runs_coll():
    return db().archive_runs


@cache
def specs_coll():
    return db().spec


@cache
def archive_specs_coll():
    return db().archive_specs


async def init():
    await runs_coll().create_index([("id", pymongo.ASCENDING)])
    await specs_coll().create_index([("trid", pymongo.ASCENDING), ("started", pymongo.ASCENDING)])


async def new_run(tr: NewTestRun):
    trdict = tr.dict()
    trdict['started'] = datetime.utcnow()
    await runs_coll().insert_one(trdict)
    if settings.ARCHIVE:
        await archive_runs_coll().insert_one(trdict)


async def set_status(trid: int, status: TestRunStatus):
    if status in INACTIVE_STATES:
        # remove all the specs and delete the run
        await specs_coll().delete_many({'trid': trid})
        await runs_coll().delete_one({'id': trid})
    else:
        await runs_coll().find_one_and_update({'id': trid}, {'$set': {'status': status}})


async def set_build_details(testrun_id: int, details: CompletedBuild) -> NewTestRun:
    await specs_coll().insert_many([{'trid': testrun_id, 'file': f, 'started': None, 'finished': None} for f in details.specs])
    tr = await runs_coll().find_one_and_update({'id': testrun_id}, {'$set': {'status': 'running',
                                                                        'cache_key': details.cache_hash}},
                                               return_document=ReturnDocument.AFTER)
    if settings.ARCHIVE:
        await archive_specs_coll().insert_many(
            [{'trid': testrun_id, 'file': f, 'started': None, 'finished': None} for f in details.specs])
        await archive_runs_coll().find_one_and_update({'id': testrun_id}, {'$set': {'status': 'running',
                                                                   'cache_key': details.cache_hash}})

    return NewTestRun.parse_obj(tr)


async def delete_testrun(trid: int):
    await specs_coll().delete_one({'trid': trid})
    await runs_coll().delete_one({'id': trid})
    await remove_testrun_artifacts(trid)


async def delete_project(project_id: int):
    trids = []
    async for doc in runs_coll().find({'project.id': project_id}, ['id']):
        trids.append(doc['id'])
    await specs_coll().delete_one({'trid': {'$in': trids}})
    for id in trids:
        await remove_testrun_artifacts(id)


async def get_testrun(testrun_id: int):
    return await runs_coll().find_one({'id': testrun_id})


async def get_inactive_testrun_ids():
    trids = []
    async for doc in runs_coll().find({'status': {'$in': INACTIVE_STATES}}, ['id']):
        trids.append(doc['id'])

    return trids


async def remove_testruns(ids):
    await specs_coll().delete_many({'trid': {'$in': ids}})
    await runs_coll().delete_many({id: {'$in': ids}})


async def get_active_specfile_docs():
    specs = []
    async for doc in specs_coll().find({'started': {'$ne': None}, 'finished': None, 'pod_name': {'$ne': None}}):
        specs.append(doc)
    return specs


async def get_testruns_with_status(status: TestRunStatus) -> list[dict]:
    runs = []
    async for doc in runs_coll().find({'status': status}):
        runs.append(doc)
    return runs


async def assign_next_spec(testrun_id: int, pod_name: str = None) -> str | None:
    toset = {'started': datetime.utcnow()}
    if pod_name:
        toset['pod_name'] = pod_name
    s = await specs_coll().find_one_and_update({'trid': testrun_id,
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
    await specs_coll().delete_one({'trid': trid, 'file': file})
    cnt = await specs_coll().count_documents({'trid': trid})
    if not cnt and not settings.ARCHIVE:
        await remove_testrun_artifacts(trid)


async def spec_terminated(trid: int, file: str):
    """
    Make it available again
    :param trid:
    :param file:
    :return:
    """
    await specs_coll().update_one({'trid': trid, 'file': file}, {'$set': {'started': None}})


async def remove_testrun_artifacts(trid: int):
    path = os.path.join(settings.CYKUBE_CACHE_DIR, f'{trid}.tar.lz4')
    if os.path.exists(path):
        await aiofiles.os.remove(path)
    await runs_coll().delete_one({'id': trid})


async def reset_specfile(specdoc):
    await specs_coll().find_one_and_update({'_id': specdoc['_id']}, {'$set': {'started': None}})


async def reset_testrun(local_id):
    tr = await archive_runs_coll().find_one({'local_id': local_id})
    pk = tr['id']
    await runs_coll().delete_one({'id': pk})
    await runs_coll().insert_one(tr)

    specs = await archive_specs_coll().find({'trid': pk}).to_list(1000)
    await specs_coll().delete_many({'trid': pk})
    await specs_coll().insert_many(specs)
