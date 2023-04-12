import asyncio
import os
from functools import cache

import aiofiles
import aiofiles.os
import aiohttp
import aioshutil
from aiofiles.ospath import exists
from aiohttp import web
from aiohttp.web_exceptions import HTTPForbidden
from aiohttp.web_middlewares import middleware
from loguru import logger

from common.settings import settings

routes = web.RouteTableDef()


@middleware
async def auth_middleware(request, handler):

    if request.path != '/api/hc':

        if request.version != aiohttp.HttpVersion11:
            return

        authval = request.headers.get('AUTHORIZATION')
        if authval:
            token = authval.split(' ')
            if len(token) != 2 or token[0] != 'Bearer' or token[1] != settings.API_TOKEN:
                raise HTTPForbidden()
        else:
            token = request.query.get('token')
            if not token or token != settings.API_TOKEN:
                raise HTTPForbidden()

    return await handler(request)


@cache
def session():
    timeout = aiohttp.ClientTimeout(total=settings.FILESTORE_TOTAL_TIMEOUT,
                                    connect=settings.FILESTORE_CONNECT_TIMEOUT,
                                    sock_connect=settings.FILESTORE_CONNECT_TIMEOUT,
                                    sock_read=settings.FILESTORE_READ_TIMEOUT)
    return aiohttp.ClientSession(timeout=timeout, headers={'Authorization': f'Bearer {settings.API_TOKEN}'})


@routes.get('/api/hc')
async def hc(request):
    return web.Response(text="OK")


@routes.get('/fs/{filename}')
async def server(request):
    """
    Fetching from the server rather than something like Nginx as a sidecar isn't the most efficient technique,
    but it does mean one less container to nail up. May as well offer it as an option
    :param request:
    :return:
    """
    filename = request.match_info['filename']
    path = await get_path_if_exists(filename)
    if not path:
        return web.Response(status=404)
    return web.FileResponse(path)


@routes.get('/api/ls')
async def get_directory(request):
    """
    Return the cache list
    :param request:
    :return:
    """
    return web.json_response([x for x in os.listdir(settings.CACHE_DIR) if not x.startswith('.')])


async def prune_cache(app, size: int):
    """
    Simple LRU cache semantics
    :param app:
    :param size:
    :return:
    """
    target_size = settings.FILESTORE_CACHE_SIZE - size
    file_and_stat = []
    for f in await aiofiles.os.listdir(settings.CACHE_DIR):
        st = await aiofiles.os.stat(os.path.join(settings.CACHE_DIR, f))
        file_and_stat.append((f, st))
    # sort by reverse access
    lru = sorted(file_and_stat, key=lambda x: x[1].st_atime, reverse=True)
    while app['stats']['size'] > target_size:
        file, fstat = lru.pop()
        logger.debug(f'Pruning {file} of size {fstat.st_size}')
        await aiofiles.os.remove(os.path.join(settings.CACHE_DIR, file))
        app['stats']['size'] -= fstat.st_size


@routes.post('/api/upload')
async def upload(request):
    """
    Store a file in the cache
    :param request:
    :return:
    """

    reader = await request.multipart()
    field = await reader.next()
    assert field.name == 'file'
    filename = field.filename
    logger.debug(f'Storing file {filename}')

    destfile = os.path.join(settings.CACHE_DIR, filename)
    if await exists(destfile):
        logger.debug(f'Ignoring {destfile} - we already have it')
        return web.Response()

    size = 0
    async with aiofiles.tempfile.NamedTemporaryFile('wb', delete=False,
                                                    dir=settings.get_temp_dir()) as f:
        while True:
            chunk = await field.read_chunk()  # 8192 bytes by default.
            if not chunk:
                break
            size += len(chunk)
            await f.write(chunk)
        # check if we have space in the cache, and prune if not
        if size > settings.FILESTORE_CACHE_SIZE:
            return web.Response(status=400, reason="Too large")
        if request.app['stats']['size'] + size >= settings.FILESTORE_CACHE_SIZE:
            await prune_cache(request.app, size)
        # now move to the cache
        await f.flush()
        await aioshutil.move(f.name, destfile)
        request.app['stats']['size'] += size
    logger.debug(f"Saved file {filename}")
    return web.Response()


@routes.post('/api/rm/{filename}')
async def delete_file(request):
    """
    Delete from the cache
    :param request:
    :return:
    """
    filename = request.match_info['filename']
    path = await get_path_if_exists(filename)
    if not path:
        return web.Response(status=404)
    await aiofiles.os.remove(path)
    return web.Response()


@routes.post('/api/clear')
async def clear_cache(request):
    for x in os.listdir(settings.CACHE_DIR):
        await aiofiles.os.remove(os.path.join(os.listdir(settings.CACHE_DIR), x))


def get_sync_hosts(app):
    """
    Determine all nodes. In practice, we are likely to just have 2 agents and therefore one other node
    :param app:
    :return:
    """
    # filter out ourselves
    hosts = app['synchosts'] = []
    for h in settings.FILESTORE_SERVERS.split(','):
        if not h.startswith(app['hostname']):
            if not h.startswith('http'):
                h = f'http://{h}'
            hosts.append(h)
    logger.info(f"Sync hosts: {app['synchosts']}")


async def on_shutdown(app):
    await session().close()


async def catch_up(app):
    """
    Very simple sync mechanism. Fetches the directory of files from all the other nodes
    and then fetches missing files
    :param app:
    :return:
    """

    while True:
        for host in app['synchosts']:
            incache = set(await aiofiles.os.listdir(settings.CACHE_DIR))
            try:
                async with session().get(f'{host}/api/ls') as resp:
                    if resp.status == 200:
                        files = set(await resp.json())
                    else:
                        logger.warning(f"Can't connect {host}")
                        continue

                topull = files - incache
                if topull:
                    # pull files from the node that are not present locally
                    for fname in topull:
                        # load it from the cache
                        logger.debug(f"Pulling {fname} from {host}")
                        async with session().get(f'{host}/fs/{fname}') as resp:
                            destpath = os.path.join(settings.CACHE_DIR, f'.{fname}')
                            async with aiofiles.open(destpath, 'wb') as f:
                                async for chunk in resp.content.iter_chunked(settings.CHUNK_SIZE):
                                    await f.write(chunk)
                            await aiofiles.os.rename(destpath, os.path.join(settings.CACHE_DIR, fname))
            except Exception as ex:
                logger.warning(f"Failed to sync from {host}: {ex}")

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




async def get_path_if_exists(filename):
    path = os.path.join(settings.CACHE_DIR, filename)
    if not await aiofiles.ospath.exists(path):
        return None
    return path
