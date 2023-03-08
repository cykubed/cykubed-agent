import asyncio
import os
from datetime import datetime

import sentry_sdk
from loguru import logger
from sentry_sdk.integrations.asyncio import AsyncioIntegration

import asyncmongo
import messages
import ws
from common import k8common, mongo
from common.enums import AgentEventType
from common.schemas import AgentStatusChanged, AgentSpecCompleted
from common.settings import settings
from logs import configure_logging

if os.environ.get('SENTRY_DSN'):
    sentry_sdk.init(integrations=[
        AsyncioIntegration(),
    ],)


async def poll_testrun_status():
    coll = asyncmongo.runs_coll()
    async with coll.watch(pipeline=[{'$match': {'operationType': 'update',
                                                'updateDescription.updatedFields.status': {'$ne': None}}}],
                          full_document='updateLookup') as stream:
        async for change in stream:
            tr = change['fullDocument']
            messages.queue.add_agent_msg(AgentStatusChanged(testrun_id=tr['id'],
                                                            type=AgentEventType.status,
                                                            status=tr['status']))


async def poll_specs():
    async with asyncmongo.specs_coll().watch(full_document='updateLookup') as stream:
        async for change in stream:
            optype = change['operationType']
            if optype == 'update':
                doc = change['fullDocument']['trid']
                trid = doc['trid']
                file = doc['file']

                finished = change['updateDescription']['updatedFields'].get('finished')
                if finished:
                    messages.queue.add_agent_msg(
                        AgentSpecCompleted(type=AgentEventType.spec_completed,
                                           testrun_id=trid,
                                           file=file,
                                           result=doc['result']))
                    # have we finished all specs?
                    cnt = await asyncmongo.specs_coll().count_documents({'trid': trid, 'finished': None})
                    if not cnt:
                        tr = await asyncmongo.runs_coll().find_one({'id': trid}, ['failures'])
                        status = 'passed' if not tr.get('failures') else 'failed'
                        await asyncmongo.runs_coll().update_one({'id': trid},
                                               {'$set': {'finished': datetime.utcnow(), 'status': status}})


async def init():
    """
    Run the websocket and server concurrently
    """
    try:
        mongo.ensure_connection()
        await asyncmongo.init()
    except:
        logger.exception("Failed to initialise MongoDB")

    # I'll need to watch collections in mongo and act accordingly: this will replace the server
    #
    await asyncio.gather(ws.connect(), poll_testrun_status(), poll_specs())


if __name__ == "__main__":
    try:
        if settings.K8 and not settings.TEST:
            k8common.init()
        configure_logging()
        asyncio.run(init())
    except Exception as ex:
        print(ex)

