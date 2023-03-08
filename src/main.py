import asyncio
import os

import sentry_sdk
from loguru import logger
from sentry_sdk.integrations.asyncio import AsyncioIntegration

import ws
from common import k8common, asyncmongo, mongo
from common.settings import settings
from logs import configure_logging

if os.environ.get('SENTRY_DSN'):
    sentry_sdk.init(integrations=[
        AsyncioIntegration(),
    ],)


async def init():
    """
    Run the websocket and server concurrently
    """
    try:
        mongo.ensure_connection()
        await asyncmongo.init()
    except:
        logger.exception("Failed to initialise MongoDB")

    # I'll need to watch collections in mongo and act accordingly: this will replace the server
    #
    await asyncio.gather(ws.connect())


if __name__ == "__main__":
    try:
        if settings.K8 and not settings.TEST:
            k8common.init()
        configure_logging()
        asyncio.run(init())
    except Exception as ex:
        print(ex)

