import asyncio
import sys
from time import sleep

from aiohttp import web
from loguru import logger

import ws
from app import app
from common import k8common
from common.k8common import close
from common.redisutils import sync_redis, ping_redis, async_redis
from jobs import prune_cache_loop, watch_pod_events
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

    if settings.K8 and not settings.TEST:
        await k8common.init()

    tasks = [asyncio.create_task(hc_server()),
             asyncio.create_task(ws.connect())]
    if app.hostname == 'agent-0':
        tasks += [asyncio.create_task(prune_cache_loop()),
                  asyncio.create_task(watch_pod_events()),
                  # asyncio.create_task(watch_job_events()), # This is broken!
                  ]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    # cancel the others
    for task in pending:
        task.cancel()


if __name__ == "__main__":
    # if settings.SENTRY_DSN:
    #     sentry_sdk.init(
    #         dsn=settings.SENTRY_DSN,
    #         integrations=[RedisIntegration(), AsyncioIntegration(),], )
    configure_logging()
    logger.info("Cykubed agent starting")

    # block until we can access Redis
    redis = sync_redis()
    while True:
        if ping_redis():
            break
        logger.debug("Cannot ping_redis Redis - waiting")
        sleep(5)

    logger.info("Connected to Redis")

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        asyncio.run(close())
        sys.exit(0)
