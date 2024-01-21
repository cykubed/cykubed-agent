from __future__ import annotations

import asyncio
import logging

import loguru
from loguru import logger

from app import app
from common import schemas
from common.enums import AgentEventType
from common.schemas import AppLogMessage

msgqueue = asyncio.Queue()


def without_keys(d, keys):
    return {x: d[x] for x in d if x not in keys}


def rest_logsink(msg: loguru.Message):
    record = msg.record
    tr = record['extra'].get('tr')
    if tr:
        id = tr.id
    else:
        id = record['extra'].get('id')
        if not id:
            id = record['extra'].get('trid')
    if id:
        item = schemas.AgentLogMessage(testrun_id=id,
                                       type=AgentEventType.log,
                                       msg=AppLogMessage(ts=msg.record['time'],
                                                         level=msg.record['level'].name.lower(),
                                                         msg=msg,
                                                         source=app.hostname))
        msgqueue.put_nowait(item.json())


def configure_logging():
    # disable logging for health check
    logging.getLogger("aiohttp.access").disabled = True
    logger.add(rest_logsink,
               format="{message}", level="INFO")
