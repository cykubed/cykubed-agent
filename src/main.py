import argparse
import asyncio
import sys

import sentry_sdk
from aiohttp import web
from loguru import logger
from sentry_sdk.integrations.asyncio import AsyncioIntegration

import logs
import ws
from app import app
from cache import delete_all_jobs, \
    delete_all_pvcs, delete_all_volume_snapshots
from common import k8common
from common.cloudlogging import configure_stackdriver_logging
from common.k8common import close
from logs import configure_logging
from settings import settings
from watchers import watch_pod_events, watch_job_events


async def handler(request):
    if request.method == 'GET' and request.path == '/':
        if not app.ws_connected:
            return web.Response(status=500)
        return web.Response(text="OK")

    if request.method == 'POST' and request.path == '/log':
        logpayload = (await request.content.read()).decode()
        logs.msgqueue.put_nowait(logpayload)
        return web.Response()


async def hc_server():
    server = web.Server(handler)
    runner = web.ServerRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', settings.PORT)
    await site.start()
    while app.is_running():
        await asyncio.sleep(60)


async def run():
    if not settings.TEST:
        await k8common.init()

    tasks = [asyncio.create_task(hc_server()),
             asyncio.create_task(ws.connect())]
    if app.hostname == 'agent-0':
        tasks += [asyncio.create_task(watch_pod_events()),
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

    if settings.SENTRY_DSN:
        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            integrations=[AsyncioIntegration(),], )

    configure_logging()
    configure_stackdriver_logging('cykubed-agent')

    if args.clear:
        logger.info("Cykubed pre-delete cleanup")
        asyncio.run(cleanup_pending_delete())
        sys.exit(0)
    else:
        logger.info("Cykubed agent starting")
        try:
            asyncio.run(run())
        except KeyboardInterrupt:
            asyncio.run(close())
            sys.exit(0)
