import aiohttp

from settings import settings

session = aiohttp.ClientSession(headers={'Authorization': f'Bearer {settings.API_TOKEN}'})


async def post_logs(trid: int, log: str):
    await session.post(f'{settings.CYKUBE_APP_URL}/hub/logs/{trid}', data=log)


