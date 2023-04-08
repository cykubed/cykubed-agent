from __future__ import annotations

import loguru
from loguru import logger

from common.cloudlogging import configure_stackdriver_logging
from messages import queue


def without_keys(d, keys):
    return {x: d[x] for x in d if x not in keys}


def rest_logsink(msg: loguru.Message):
    record = msg.record
    tr = record['extra'].get('tr')
    if tr:
        id = tr.id
    else:
        id = record['extra'].get('id')
    if id:
        queue.send_log('agent', id, msg)


def configure_logging():
    configure_stackdriver_logging('cykube-agent')
    logger.add(rest_logsink,
               format="{message}", level="INFO")
