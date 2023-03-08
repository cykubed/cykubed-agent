import asyncio
import os
from datetime import datetime

import sentry_sdk
from loguru import logger
from sentry_sdk.integrations.asyncio import AsyncioIntegration

import messages
import ws
from asyncmongo import specs_coll, messages_coll, runs_coll, init_indexes
from common import k8common, mongo
from common.enums import AgentEventType
from common.schemas import AgentEvent
from common.settings import settings
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


async def poll_messages(max_messages=None):
    """
    Poll the message queue, forwarding them all to the websocket
    :param max_messages: limit the number of messages sent (for unit testing)
    """
    sent = 0
    while True:
        msgdoc = await messages_coll().find_one_and_delete({}, projection={'_id': False})
        if msgdoc:
            msg = msgdoc['msg']
            messages.queue.add_agent_msg(msg)
            event = AgentEvent.parse_raw(msg)
            if event.type == AgentEventType.spec_completed:
                await check_for_completion(event.testrun_id)

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
        mongo.ensure_connection()
        await init_indexes()
    except:
        logger.exception("Failed to initialise MongoDB")

    await asyncio.gather(ws.connect(), poll_messages())


if __name__ == "__main__":
    try:
        if settings.K8 and not settings.TEST:
            k8common.init()
        configure_logging()
        asyncio.run(init())
    except Exception as ex:
        print(ex)

