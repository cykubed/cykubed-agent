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
def specs_coll():
    return db().spec


async def init():
    await runs_coll().create_index([("id", pymongo.ASCENDING)])
    await specs_coll().create_index([("trid", pymongo.ASCENDING), ("started", pymongo.ASCENDING)])


async def new_run(tr: NewTestRun):
    trdict = tr.dict()
    trdict['started'] = utcnow()
    await runs_coll().insert_one(trdict)


async def set_status(trid: int, status: TestRunStatus):
    if status in INACTIVE_STATES:
        # remove all the specs and delete the run
        await specs_coll().delete_many({'trid': trid})
        await runs_coll().delete_one({'id': trid})
    else:
        await runs_coll().find_one_and_update({'id': trid}, {'$set': {'status': status}})


async def set_build_details(testrun_id: int, details: CompletedBuild) -> NewTestRun:
    await specs_coll().insert_many([{'trid': testrun_id, 'file': f, 'started': None, 'finished': None}
                                    for f in details.specs])
    tr = await runs_coll().find_one_and_update({'id': testrun_id}, {'$set': {'status': 'running',
                                                                             'sha': details.sha,
                                                                             'cache_key': details.cache_hash}},
                                               return_document=ReturnDocument.AFTER)
    return NewTestRun.parse_obj(tr)


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


async def get_testrun(testrun_id: int):
    return await runs_coll().find_one({'id': testrun_id})


async def get_stale_testruns():
    testruns = []
    dt = utcnow() - timedelta(seconds=settings.APP_DISTRIBUTION_CACHE_TTL)
    async for doc in runs_coll().find({'finished': {'$ne': None}, 'started': {'$lt': dt}}):
        testruns.append(doc)
    return testruns


async def remove_testruns(ids):
    await specs_coll().delete_many({'trid': {'$in': ids}})
    await runs_coll().delete_many({'id': {'$in': ids}})


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


async def spec_completed(trid: int, item: CompletedSpecFile):
    """
    Remove the spec and update the test run
    :param trid:
    :param item:
    :return:
    """
    await specs_coll().delete_one({'trid': trid, 'file': item.file})
    cnt = await specs_coll().count_documents({'trid': trid})
    failures = len([t for t in item.result.tests if t.error])
    await runs_coll().update_one({'id': trid}, {'$inc': {'failures': failures}})
    if not cnt:
        tr = await runs_coll().find_one({'id': trid}, ['failures'])
        status = 'passed' if not tr.get('failures') else 'failed'
        await runs_coll().update_one({'id': trid}, {'$set': {'finished': utcnow(), 'status': status}})


async def spec_terminated(trid: int, file: str):
    """
    Make it available again
    :param trid:
    :param file:
    :return:
    """
    await specs_coll().update_one({'trid': trid, 'file': file}, {'$set': {'started': None}})
    await specs_coll().update_one({'trid': trid, 'file': file}, {'$set': {'started': None}})

