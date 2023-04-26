import httpx

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


app = App()



