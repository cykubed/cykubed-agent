import asyncio
import os
import sys
from datetime import datetime
from time import sleep

import sentry_sdk
from loguru import logger
from sentry_sdk.integrations.asyncio import AsyncioIntegration

import jobs
import messages
import ws
from asyncmongo import specs_coll, messages_coll, runs_coll, init_indexes
from common import k8common
from common.enums import AgentEventType
from common.schemas import AgentEvent, AgentCompletedBuildMessage, NewTestRun
from common.settings import settings
from common.utils import ensure_mongo_connection
from logs import configure_logging

if os.environ.get('SENTRY_DSN'):
    sentry_sdk.init(integrations=[
        AsyncioIntegration(),
    ],)


async def check_for_completion(testrun_id: int):
    # have we finished all specs?
    cnt = await specs_coll().count_documents({'trid': testrun_id, 'finished': None})
    if not cnt:
        # yep - collate the number of failures
        tr = await runs_coll().find_one({'id': testrun_id}, ['failures'])
        status = 'passed' if not tr.get('failures') else 'failed'
        await runs_coll().update_one({'id': testrun_id},
                                     {'$set': {'finished': datetime.utcnow(), 'status': status}})


async def build_completed(build: AgentCompletedBuildMessage):
    pass


async def poll_messages(max_messages=None):
    """
    Poll the message queue, forwarding them all to the websocket
    :param max_messages: limit the number of messages sent (for unit testing)
    """
    sent = 0
    while True:
        docs = await messages_coll().find().to_list(length=200)
        if docs:
            try:
                for msgdoc in docs:
                    msg = msgdoc['msg']
                    event = AgentEvent.parse_raw(msg)
                    messages.queue.add(msg)
                    if event.type == AgentEventType.spec_completed:
                        await check_for_completion(event.testrun_id)
                    elif event.type == AgentEventType.build_completed:
                        buildmsg: AgentCompletedBuildMessage = AgentCompletedBuildMessage.parse_raw(msg)
                        testrun = NewTestRun.parse_obj(await runs_coll().find_one({'id': buildmsg.testrun_id}))
                        jobs.create_runner_jobs(testrun, buildmsg)
            finally:
                await messages_coll().delete_many({'_id': {'$in': [x['_id'] for x in docs]}})

            # this is just to make it easier to test
            sent += 1
            if max_messages and sent >= max_messages:
                return
        await asyncio.sleep(settings.MESSAGE_POLL_PERIOD)


async def init():
    """
    Run the websocket and server concurrently
    """
    try:
        ensure_mongo_connection()
    except:
        logger.exception("Failed to connect to MongoDB")
        sleep(3600)
        sys.exit(1)

    await init_indexes()
    await asyncio.gather(ws.connect(), poll_messages())


if __name__ == "__main__":
    try:
        if settings.K8 and not settings.TEST:
            k8common.init()
        configure_logging()
        asyncio.run(init())
    except KeyboardInterrupt:
        pass
    except Exception as ex:
        logger.exception("Agent quit expectedly")

