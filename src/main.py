import asyncio
import sys
from time import sleep

import sentry_sdk
from loguru import logger
from sentry_sdk.integrations.asyncio import AsyncioIntegration
from sentry_sdk.integrations.redis import RedisIntegration

import ws
from app import shutdown
from common import k8common
from common.redisutils import sync_redis, ping_redis
from logs import configure_logging
from settings import settings

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

        app = dict()
        if settings.HOSTNAME:
            app['hostname'] = settings.HOSTNAME
        else:
            with open('/etc/hostname', 'r') as f:
                app['hostname'] = f.read().strip()
        asyncio.run(ws.connect(app))

    except KeyboardInterrupt:
        shutdown()
    except Exception as ex:
        logger.exception("Agent quit expectedly")
        sys.exit(1)
