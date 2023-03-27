import argparse
import asyncio
import os
import sys

import sentry_sdk
from aiohttp import web
from loguru import logger
from sentry_sdk.integrations.asyncio import AsyncioIntegration
from sentry_sdk.integrations.redis import RedisIntegration

import fsserver
import ws
from common import k8common
from common.db import sync_redis
from common.settings import settings
from logs import configure_logging
from ws import shutdown


async def background_tasks(app):
    app['catch_up'] = asyncio.create_task(fsserver.catch_up(app))
    app['connect'] = asyncio.create_task(ws.connect(app))
    app['poll_messages'] = asyncio.create_task(ws.poll_messages())

    yield

    for x in ['catch_up', 'connect', 'poll_messages']:
        app[x].cancel()
        await app[x]


async def app_factory(cache_size: int):
    if not os.path.exists(settings.CACHE_DIR):
        os.makedirs(settings.CACHE_DIR)

    app = web.Application(middlewares=[fsserver.auth_middleware])
    if settings.HOSTNAME:
        hostname = settings.HOSTNAME
    else:
        with open('/etc/hostname', 'r') as f:
            hostname = f.read().strip()
    app['hostname'] = hostname
    app['stats'] = {'size': cache_size}

    # logger.info(f'Cache is currently {cache_size} bytes ({settings.FILESTORE_CACHE_SIZE - cache_size} remaining)')
    # logger_format = (
    #     "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    #     "<level>{level: <8}</level> | "
    #     "{extra[hostname]: ^12} | "
    #     # "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
    #     "<level>{message}</level>"
    # )
    # logger.configure(extra=dict(hostname=hostname))
    # logger.remove()
    # logger.add(sys.stderr, format=logger_format)

    logger.info("Starting cache replica")

    fsserver.get_sync_hosts(app)
    app.add_routes(fsserver.routes)
    app.cleanup_ctx.append(background_tasks)
    app.on_shutdown.append(fsserver.on_shutdown)
    return app


def start(port: int):
    sz = 0
    for f in os.listdir(settings.CACHE_DIR):
        if f.startswith('.'):
            os.remove(os.path.join(settings.CACHE_DIR, f))
        st = os.stat(os.path.join(settings.CACHE_DIR, f))
        sz += st.st_size

    web.run_app(app_factory(sz), port=port)


if __name__ == "__main__":

    if settings.SENTRY_DSN:
        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            integrations=[RedisIntegration(), AsyncioIntegration(),], )

    parser = argparse.ArgumentParser('CykubeAgent')
    parser.add_argument('--port', type=int, default=8100, help='Port')
    args = parser.parse_args()

    # block until we can access Redis
    sync_redis()

    try:
        if settings.K8 and not settings.TEST:
            k8common.init()
        configure_logging()
        start(args.port)
    except KeyboardInterrupt:
        shutdown()
    except Exception as ex:
        logger.exception("Agent quit expectedly")
        sys.exit(1)
