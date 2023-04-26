import httpx
from loguru import logger

from common.enums import TestRunStatus
from settings import settings


class App(object):
    def __init__(self):
        self.running = True
        self.ws = None
        if settings.HOSTNAME:
            self.hostname = settings.HOSTNAME
        else:
            with open('/etc/hostname', 'r') as f:
                self.hostname = f.read().strip()
        transport = httpx.AsyncHTTPTransport(retries=settings.MAX_HTTP_RETRIES)
        self.httpclient = httpx.AsyncClient(transport=transport,
                                   base_url=settings.MAIN_API_URL,
                                   headers={'Authorization': f'Bearer {settings.API_TOKEN}'})

    def is_running(self) -> bool:
        return self.running

    async def shutdown(self):
        self.running = False
        await self.httpclient.aclose()

    async def update_status(self, testrun_id, status: TestRunStatus):
        resp = await self.httpclient.post(f'/agent/testrun/{testrun_id}/status/{status}')
        if resp.status_code != 200:
            logger.error(f'Failed to update server with new state')


app = App()



