from datetime import datetime, timedelta
from functools import cache

import aiofiles
import pymongo
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument

from cache import get_app_distro_filename
from common.enums import INACTIVE_STATES, TestRunStatus
from common.schemas import NewTestRun, CompletedBuild, CompletedSpecFile
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
def async_runs_coll():
    return async_db().runs


@cache
def async_specs_coll():
    return async_db().spec


async def init():
    await async_runs_coll().create_index([("id", pymongo.ASCENDING)])
    await async_specs_coll().create_index([("trid", pymongo.ASCENDING), ("started", pymongo.ASCENDING)])


async def new_run(tr: NewTestRun):
    trdict = tr.dict()
    trdict['started'] = utcnow()
    await async_runs_coll().insert_one(trdict)


async def set_status(trid: int, status: TestRunStatus):
    if status in INACTIVE_STATES:
        # remove all the specs and delete the run
        await async_specs_coll().delete_many({'trid': trid})
        await async_runs_coll().delete_one({'id': trid})
    else:
        await async_runs_coll().find_one_and_update({'id': trid}, {'$set': {'status': status}})


async def set_build_details(testrun_id: int, details: CompletedBuild) -> NewTestRun:
    await async_specs_coll().insert_many([{'trid': testrun_id, 'file': f, 'started': None, 'finished': None}
                                          for f in details.specs])
    tr = await async_runs_coll().find_one_and_update({'id': testrun_id}, {'$set': {'status': 'running',
                                                                             'sha': details.sha,
                                                                             'cache_key': details.cache_hash}},
                                                     return_document=ReturnDocument.AFTER)
    return NewTestRun.parse_obj(tr)


async def cancel_testrun(trid: int):
    await async_specs_coll().delete_many({'trid': trid})
    await async_runs_coll().update_one({'id': trid}, {'$set': {'status': 'cancelled'}})


async def delete_project(project_id: int):
    trs = []
    async for doc in async_runs_coll().find({'project.id': project_id}):
        trs.append(doc)
    await async_specs_coll().delete_one({'trid': {'$in': [x['id'] for x in trs]}})
    for tr in trs:
        path = get_app_distro_filename(tr)
        if await aiofiles.os.path.exists(path):
            await aiofiles.os.remove(path)
        await async_runs_coll().delete_one({'_id': tr['_id']})


async def get_testrun(testrun_id: int):
    return await async_runs_coll().find_one({'id': testrun_id})


async def get_stale_testruns():
    testruns = []
    dt = utcnow() - timedelta(seconds=settings.APP_DISTRIBUTION_CACHE_TTL)
    async for doc in async_runs_coll().find({'finished': {'$ne': None}, 'started': {'$lt': dt}}):
        testruns.append(doc)
    return testruns


async def remove_testruns(ids):
    await async_specs_coll().delete_many({'trid': {'$in': ids}})
    await async_runs_coll().delete_many({'id': {'$in': ids}})


async def get_active_specfile_docs():
    specs = []
    async for doc in async_specs_coll().find({'started': {'$ne': None}, 'finished': None, 'pod_name': {'$ne': None}}):
        specs.append(doc)
    return specs


async def get_testruns_with_status(status: TestRunStatus) -> list[dict]:
    runs = []
    async for doc in async_runs_coll().find({'status': status}):
        runs.append(doc)
    return runs


async def assign_next_spec(testrun_id: int, pod_name: str = None) -> str | None:
    toset = {'started': datetime.utcnow()}
    if pod_name:
        toset['pod_name'] = pod_name
    s = await async_specs_coll().find_one_and_update({'trid': testrun_id,
                                                   'started': None},
                                                     {'$set': toset})
    if s:
        return s['file']


async def spec_completed(trid: int, item: CompletedSpecFile):
    """
    Remove the spec and update the test run
    :param trid:
    :param item:
    :return:
    """
    await async_specs_coll().delete_one({'trid': trid, 'file': item.file})
    cnt = await async_specs_coll().count_documents({'trid': trid})
    failures = len([t for t in item.result.tests if t.error])
    await async_runs_coll().update_one({'id': trid}, {'$inc': {'failures': failures}})
    if not cnt:
        tr = await async_runs_coll().find_one({'id': trid}, ['failures'])
        status = 'passed' if not tr.get('failures') else 'failed'
        await async_runs_coll().update_one({'id': trid}, {'$set': {'finished': utcnow(), 'status': status}})


async def spec_terminated(trid: int, file: str):
    """
    Make it available again
    :param trid:
    :param file:
    :return:
    """
    await async_specs_coll().update_one({'trid': trid, 'file': file}, {'$set': {'started': None}})
    await async_specs_coll().update_one({'trid': trid, 'file': file}, {'$set': {'started': None}})

