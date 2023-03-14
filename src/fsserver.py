import argparse
import asyncio
import os
import sys
from functools import cache

import aiofiles
import aiofiles.os
import aiohttp
from aiofiles.ospath import exists
from aiohttp import web
from loguru import logger

from common.settings import settings

routes = web.RouteTableDef()


@cache
def session():
    timeout = aiohttp.ClientTimeout(total=settings.FILESTORE_TOTAL_TIMEOUT,
                                    connect=settings.FILESTORE_CONNECT_TIMEOUT,
                                    sock_connect=settings.FILESTORE_CONNECT_TIMEOUT,
                                    sock_read=settings.FILESTORE_READ_TIMEOUT)
    return aiohttp.ClientSession(timeout=timeout)


@routes.get('/hc')
async def hc(request):
    return web.Response(text="OK")


@routes.get('/{filename}')
async def server(request):
    filename = request.match_info['filename']
    path = await get_path_if_exists(filename)
    if not path:
        return web.Response(status=404)
    return web.FileResponse(path)


@routes.get('/')
async def get_directory(request):
    return web.json_response([x for x in os.listdir(settings.CACHE_DIR) if not x.startswith('.')])


@routes.post('/')
async def upload(request):
    reader = await request.multipart()
    field = await reader.next()
    assert field.name == 'file'
    filename = field.filename
    logger.info(f'Storing file {filename}')

    destfile = os.path.join(settings.CACHE_DIR, filename)
    if await exists(destfile):
        logger.info(f'Ignoring {destfile} - we already have it')
        return web.Response()

    # You cannot rely on Content-Length if transfer is chunked.
    size = 0
    tmpname = f'{settings.CACHE_DIR}/.{filename}'
    async with aiofiles.open(tmpname, 'wb') as f:
        while True:
            chunk = await field.read_chunk()  # 8192 bytes by default.
            if not chunk:
                break
            size += len(chunk)
            await f.write(chunk)
    # rename file
    await aiofiles.os.rename(tmpname, destfile)
    logger.info(f"Saved file {filename}")
    return web.Response()


@routes.delete('/{filename}')
async def delete_file(request):
    filename = request.match_info['filename']
    path = await get_path_if_exists(filename)
    if not path:
        return web.Response(status=404)
    await aiofiles.os.remove(path)
    return web.Response()


def get_sync_hosts(app):
    # filter out ourselves
    app['synchosts'] = [h for h in settings.FILESTORE_SERVERS.split(',') if not h.startswith(app['hostname'])]
    logger.info(f"Sync hosts: {app['synchosts']}")


async def on_shutdown(app):
    await session().close()


async def catch_up(app):
    while True:
        for host in app['synchosts']:
            incache = set(await aiofiles.os.listdir(settings.CACHE_DIR))
            try:
                logger.info(f"Syncing with {host}:")
                async with session().get(f'http://{host}') as resp:
                    files = set(await resp.json())

                topull = files - incache
                if not topull:
                    logger.info(f"Cache is up to date")
                else:
                    # pull files from the load balancer if they are in the master index but not local
                    logger.info(f"Need to pull {len(topull)} files into local cache")
                    for fname in topull:
                        # load it from the cache
                        async with session().get(f'http://{host}/{fname}') as resp:
                            destpath = os.path.join(settings.CACHE_DIR, f'.{fname}')
                            async with aiofiles.open(destpath, 'wb') as f:
                                async for chunk in resp.content.iter_chunked(settings.CHUNK_SIZE):
                                    await f.write(chunk)
                            await aiofiles.os.rename(destpath, os.path.join(settings.CACHE_DIR, fname))
                        logger.info(f"Pulled file {fname} into cache")
                    logger.info(f"Finished sync with {host}")
            except Exception as ex:
                logger.warning(f"Failed to pull from {host}: {ex}")

        await asyncio.sleep(settings.FILESTORE_SYNC_PERIOD)

# async def prune(app):
#     r = app['redis']
#     while True:
#         for filename in os.listdir(settings.CACHE_DIR):
#             last_access = await r.get(f'{filename}:last_access')
#             if last_access:
#                 last_access_ts = float(last_access)
#                 if time.time() - last_access_ts > CACHE_TTL:
#                     # remove from master list
#                     await session().delete(f'{CACHE_URL}/{filename}')
#
#         await asyncio.sleep(CACHE_PRUNE_PERIOD)


async def background_tasks(app):
    app['catch_up'] = asyncio.create_task(catch_up(app))

    yield

    app['catch_up'].cancel()
    await app['catch_up']


async def app_factory():
    if not os.path.exists(settings.CACHE_DIR):
        os.makedirs(settings.CACHE_DIR)

    app = web.Application()
    if settings.HOSTNAME:
        hostname = settings.HOSTNAME
    else:
        with open('/etc/hostname', 'r') as f:
            hostname = f.read().strip()
    app['hostname'] = hostname

    logger_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "{extra[hostname]: ^12} | "
        # "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    )
    logger.configure(extra=dict(hostname=hostname))
    logger.remove()
    logger.add(sys.stderr, format=logger_format)

    logger.info("Starting cache replica")

    get_sync_hosts(app)
    app.add_routes(routes)
    app.cleanup_ctx.append(background_tasks)
    app.on_shutdown.append(on_shutdown)
    return app


def start(port: int):
    for f in os.listdir(settings.CACHE_DIR):
        if f.startswith('.'):
            os.remove(os.path.join(settings.CACHE_DIR, f))
    web.run_app(app_factory(), port=port)


async def get_path_if_exists(filename):
    path = os.path.join(settings.CACHE_DIR, filename)
    if not await aiofiles.ospath.exists(path):
        return None
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('-p', '--port', type=int, default=8100, help='Port')
    args = parser.parse_args()
    start(args.port)
