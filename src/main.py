import asyncio
import sys
from time import sleep

import sentry_sdk
from loguru import logger
from sentry_sdk.integrations.asyncio import AsyncioIntegration
from sentry_sdk.integrations.redis import RedisIntegration

import ws
from common import k8common
from common.redisutils import sync_redis, ping_redis
from jobs import prune_cache_loop
from logs import configure_logging
from settings import settings


async def run():
    done, pending = await asyncio.wait(
        [asyncio.create_task(prune_cache_loop()),
         asyncio.create_task(ws.connect())], return_when=asyncio.FIRST_COMPLETED,
    )
    # cancel the others
    for task in pending:
        task.cancel()


if __name__ == "__main__":
    if settings.SENTRY_DSN:
        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            integrations=[RedisIntegration(), AsyncioIntegration(),], )

    # block until we can access Redis
    redis = sync_redis()
    while True:
        if ping_redis():
            break
        logger.debug("Cannot ping_redis Redis - waiting")
        sleep(5)

    if settings.K8 and not settings.TEST:
        k8common.init()
    configure_logging()
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        sys.exit(0)
