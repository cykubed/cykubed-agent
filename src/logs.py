from __future__ import annotations

import loguru
from loguru import logger

from common import schemas
from common.cloudlogging import configure_stackdriver_logging
from common.enums import AgentEventType
from common.redisutils import sync_redis
from common.schemas import AppLogMessage
from common.utils import disable_hc_logging


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
                                                         source='agent'))

        sync_redis().rpush('messages', item.json())


def configure_logging():
    disable_hc_logging()
    configure_stackdriver_logging('cykube-agent')
    logger.add(rest_logsink,
               format="{message}", level="INFO")
