import argparse
import asyncio
import sys
from time import sleep

from aiohttp import web
from loguru import logger

import ws
from app import app
from cache import prune_cache_loop, garage_collect_loop, delete_all_jobs, \
    delete_all_pvcs, delete_all_volume_snapshots
from common import k8common
from common.k8common import close
from common.redisutils import sync_redis, ping_redis, async_redis
from logs import configure_logging
from settings import settings
from watchers import watch_pod_events, watch_job_events


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
    sync_redis()
    # block until we can access Redis
    while True:
        if ping_redis():
            break
        logger.debug("Cannot ping_redis Redis - waiting")
        sleep(5)

    logger.info("Connected to Redis")

    if not settings.TEST:
        await k8common.init()

    tasks = [asyncio.create_task(hc_server()),
             asyncio.create_task(ws.connect())]
    if app.hostname == 'agent-0':
        tasks += [asyncio.create_task(prune_cache_loop()),
                  asyncio.create_task(garage_collect_loop()),
                  asyncio.create_task(watch_pod_events()),
                  asyncio.create_task(watch_job_events()),
                  ]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    # cancel the others
    for task in pending:
        task.cancel()


async def cleanup_pending_delete():
    await k8common.init()
    await delete_all_jobs()
    await delete_all_pvcs()
    await delete_all_volume_snapshots()
    await app.shutdown()


if __name__ == "__main__":

    parser = argparse.ArgumentParser('Cykubed Agent')
    parser.add_argument('--clear', action='store_true', help='Clear the cache and then exist')
    args = parser.parse_args()

    configure_logging()
    if args.clear:
        logger.info("Cykubed pre-delete cleanup")
        asyncio.run(cleanup_pending_delete())
        sys.exit(0)
    else:
        logger.info("Cykubed agent starting")
        # if settings.SENTRY_DSN:
        #     sentry_sdk.init(
        #         dsn=settings.SENTRY_DSN,
        #         integrations=[RedisIntegration(), AsyncioIntegration(),], )
        try:
            asyncio.run(run())
        except KeyboardInterrupt:
            asyncio.run(close())
            sys.exit(0)
