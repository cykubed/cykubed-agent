import asyncio
import sys
from time import sleep

import sentry_sdk
from loguru import logger
from sentry_sdk.integrations.asyncio import AsyncioIntegration
from sentry_sdk.integrations.redis import RedisIntegration

import ws
from app import app
from common import k8common
from common.redisutils import sync_redis, ping_redis
from logs import configure_logging
from settings import settings


async def prune_cache():
    while app.is_running():
        await asyncio.sleep(300)
        # TODO prune cache


async def run():
    await asyncio.wait(
        [asyncio.create_task(prune_cache()),
         asyncio.create_task(ws.connect())], return_when=asyncio.FIRST_COMPLETED,
    )


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

    try:
        if settings.K8 and not settings.TEST:
            k8common.init()
        configure_logging()

        asyncio.run(run())

    except KeyboardInterrupt:
        app.shutdown()
    except Exception as ex:
        logger.exception("Agent quit expectedly")
        sys.exit(1)
