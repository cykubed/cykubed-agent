import asyncio
import sys
from time import sleep

import sentry_sdk
from aiohttp import web
from loguru import logger
from sentry_sdk.integrations.asyncio import AsyncioIntegration
from sentry_sdk.integrations.redis import RedisIntegration

import ws
from app import app
from common import k8common
from common.redisutils import sync_redis, ping_redis, async_redis
from jobs import prune_cache_loop, run_job_tracker
from logs import configure_logging
from settings import settings


async def handler(request):
    if not await async_redis().ping() or not app.ws_connected:
        return web.Response(status=500)
    return web.Response(text="OK")


async def hc_server():
    server = web.Server(handler)
    runner = web.ServerRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', settings.PORT)
    await site.start()
    while app.is_running():
        await asyncio.sleep(60)


async def run():
    tasks = [asyncio.create_task(hc_server()),
             asyncio.create_task(ws.connect())]
    if app.hostname == 'agent-0':
        tasks += [asyncio.create_task(prune_cache_loop()),
                  asyncio.create_task(run_job_tracker())]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
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
