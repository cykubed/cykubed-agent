from __future__ import annotations

import logging

import loguru
from loguru import logger

from app import app
from common import schemas
from common.cloudlogging import configure_stackdriver_logging
from common.enums import AgentEventType
from common.redisutils import sync_redis
from common.schemas import AppLogMessage


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

        sync_redis().rpush('messages', item.model_dump_json())


def configure_logging():
    # disable logging for health check
    logging.getLogger("aiohttp.access").disabled = True
    configure_stackdriver_logging('cykubed-agent')
    logger.add(rest_logsink,
               format="{message}", level="INFO")
