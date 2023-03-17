import argparse
import asyncio
import os
import sys

import sentry_sdk
from loguru import logger
from sentry_sdk.integrations.asyncio import AsyncioIntegration
from sentry_sdk.integrations.redis import RedisIntegration

import fsserver
from common import k8common
from common.db import sync_redis
from common.settings import settings
from logs import configure_logging
from ws import init, shutdown

if __name__ == "__main__":

    if os.environ.get('SENTRY_DSN'):
        sentry_sdk.init(integrations=[
            RedisIntegration(),
            AsyncioIntegration(),
        ], )

    parser = argparse.ArgumentParser('CykubeAgent')
    parser.add_argument('--port', type=int, default=8100, help='Port')
    parser.add_argument('command', choices=['agent', 'fs'], help='Command')
    args = parser.parse_args()

    if args.command == 'agent':
        # block until we can access Redis
        sync_redis()

        try:
            if settings.K8 and not settings.TEST:
                k8common.init()
            configure_logging()
            asyncio.run(init())
        except KeyboardInterrupt:
            shutdown()
        except Exception as ex:
            logger.exception("Agent quit expectedly")
            sys.exit(1)
    else:
        try:
            fsserver.start(args.port)
        except Exception as ex:
            logger.exception("Filestore quit expectedly")
            sys.exit(1)
