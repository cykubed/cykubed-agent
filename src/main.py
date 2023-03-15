import asyncio
import os

import sentry_sdk
from loguru import logger
from sentry_sdk.integrations.asyncio import AsyncioIntegration
from sentry_sdk.integrations.redis import RedisIntegration

import jobs
import messages
import ws
from common import k8common, db
from common.db import redis
from common.enums import AgentEventType
from common.schemas import AgentEvent, AgentCompletedBuildMessage
from common.settings import settings
from logs import configure_logging


async def poll_messages(max_messages=None):
    """
    Poll the message queue, forwarding them all to the websocket
    :param max_messages: limit the number of messages sent (for unit testing)
    """
    sent = 0
    async with redis().pubsub() as pubsub:
        await pubsub.psubscribe('messages')
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message is not None:
                msg = message['data']
                event = AgentEvent.parse_raw(msg)
                messages.queue.add(msg)
                if event.type == AgentEventType.build_completed:
                    buildmsg: AgentCompletedBuildMessage = AgentCompletedBuildMessage.parse_raw(msg)
                    tr = await db.get_testrun(buildmsg.testrun_id)
                    jobs.create_runner_jobs(tr, buildmsg)
                sent += 1
                if max_messages and sent == max_messages:
                    # for easier testing
                    return


async def init():
    """
    Run the websocket and server concurrently
    """
    await asyncio.gather(ws.connect(), poll_messages())


if __name__ == "__main__":

    if os.environ.get('SENTRY_DSN'):
        sentry_sdk.init(integrations=[
            RedisIntegration(),
            AsyncioIntegration(),
        ], )

    # block until we can access Redis
    redis()

    try:
        if settings.K8 and not settings.TEST:
            k8common.init()
        configure_logging()
        asyncio.run(init())
    except KeyboardInterrupt:
        pass
    except Exception as ex:
        logger.exception("Agent quit expectedly")

