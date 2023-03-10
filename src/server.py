import asyncio
import os

from aiofiles.ospath import exists
from aiohttp import web
from loguru import logger

PORT = int(os.environ.get('PORT', 8200))
SERVERS = os.environ.get('SERVERS', 'localhost:8200,localhost:8201').split(',')
CACHE_DIR = os.environ.get('CACHE_DIR', '/tmp/cykube/cache')

routes = web.RouteTableDef()


queue = asyncio.Queue(maxsize=1000)


@routes.get('/hc')
async def hc(request):
    return web.Response(text="OK")


@routes.post('/upload')
async def upload(request):
    reader = await request.multipart()
    field = await reader.next()
    assert field.name == 'file'
    filename = field.filename

    destfile = os.path.join(CACHE_DIR, filename)
    if await exists(destfile):
        logger.info(f'Ignoring {destfile} - we already have it')
        return web.Response()

    # You cannot rely on Content-Length if transfer is chunked.
    size = 0
    with open(destfile, 'wb') as f:
        while True:
            chunk = await field.read_chunk()  # 8192 bytes by default.
            if not chunk:
                break
            size += len(chunk)
            f.write(chunk)

    return web.Response()


async def syncer():
    with open('/etc/hostname', 'r') as f:
        hostname = f.read()

    # In K8, this will fetch all servers!
    # servers = dns.resolver.resolve('cache.cykube.svc.cluster.local')

    hosts = []
    for host in SERVERS:
        if not host.startswith(hostname):
            hosts.append(host)

    try:
        file = await queue.get()
        # we need to send this file to the other servers


    except asyncio.CancelledError:
        pass


async def background_tasks(app):
    app['syncer'] = asyncio.create_task(syncer())
    yield
    app['syncer'].cancel()
    await app['syncer']


if __name__ == "__main__":
    if not os.path.exists(CACHE_DIR):
        os.makedirs(CACHE_DIR)
    app = web.Application()
    app.cleanup_ctx.append(background_tasks)
    app.add_routes(routes)
    web.run_app(app, port=PORT)
