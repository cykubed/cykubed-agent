from __future__ import annotations

import loguru
from loguru import logger

from messages import queue


def rest_logsink(msg: loguru.Message):
    record = msg.record
    id = record['extra'].get('id')
    if id:
        queue.send_log('agent', id, msg)


def configure_logging():
    logger.add(rest_logsink,
               format="{message}", level="INFO")
