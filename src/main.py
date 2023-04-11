import argparse
import asyncio
import os
import sys

import httpx
import sentry_sdk
from aiohttp import web
from loguru import logger
from sentry_sdk.integrations.asyncio import AsyncioIntegration
from sentry_sdk.integrations.redis import RedisIntegration

import fsserver
import messages
import ws
from app import shutdown
from common import k8common
from common.redisutils import sync_redis
from common.settings import settings
from logs import configure_logging


async def background_tasks(app):
    app['catch_up'] = asyncio.create_task(fsserver.catch_up(app))
    app['connect'] = asyncio.create_task(ws.connect(app))
    app['poll_messages'] = asyncio.create_task(ws.poll_messages(app=app))

    yield

    for x in ['catch_up', 'connect', 'poll_messages']:
        app[x].cancel()
        await app[x]


async def close_client(app):
    if 'httpclient' in app:
        await app['httpclient'].aclose()


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

    await messages.queue.init()

    transport = httpx.AsyncHTTPTransport(retries=settings.MAX_HTTP_RETRIES)
    app['httpclient'] = httpx.AsyncClient(transport=transport,
                                          base_url=settings.MAIN_API_URL,
                                          headers={'Authorization': f'Bearer {settings.API_TOKEN}'})

    logger.info("Starting cache replica")

    fsserver.get_sync_hosts(app)
    app.add_routes(fsserver.routes)
    app.cleanup_ctx.append(background_tasks)
    app.on_shutdown.append(fsserver.on_shutdown)
    app.on_shutdown.append(close_client)
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
