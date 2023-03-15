import asyncio
import os

import sentry_sdk
from loguru import logger
from sentry_sdk.integrations.asyncio import AsyncioIntegration

import jobs
import messages
import ws
from common import k8common, db
from common.db import async_redis, sync_redis
from common.enums import AgentEventType
from common.schemas import AgentEvent, AgentCompletedBuildMessage
from common.settings import settings
from logs import configure_logging

if os.environ.get('SENTRY_DSN'):
    sentry_sdk.init(integrations=[
        AsyncioIntegration(),
    ],)


async def poll_messages(max_messages=None):
    """
    Poll the message queue, forwarding them all to the websocket
    :param max_messages: limit the number of messages sent (for unit testing)
    """
    sent = 0
    async with async_redis().pubsub() as pubsub:
        await pubsub.psubscribe('messages')
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message is not None:
                msg = message['data']
                event = AgentEvent.parse_raw(msg)
                messages.queue.add(msg)
                if event.type == AgentEventType.build_completed:
                    buildmsg: AgentCompletedBuildMessage = AgentCompletedBuildMessage.parse_raw(msg)
                    tr = await db.build_completed(buildmsg)
                    jobs.create_runner_jobs(tr, buildmsg)
                sent += 1
                if max_messages and sent == max_messages:
                    return


# async def cleanup_cache_poll():
#     while True:
#         await asyncio.sleep(300)
#         await cleanup_cache()


async def init():
    """
    Run the websocket and server concurrently
    """
    # block until we can access Redis
    sync_redis()

    await asyncio.gather(ws.connect(), poll_messages()) #, cleanup_cache_poll())


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

