from __future__ import annotations

import loguru
from loguru import logger

from messages import queue


async def rest_logsink(msg: loguru.Message):
    record = msg.record
    tr = record['extra'].get('tr')
    if tr:
        await queue.send_log('agent', tr.project.id, tr.local_id, msg)
    else:
        local_id = record['extra'].get('local_id')
        project_id = record['extra'].get('project_id')
        if local_id and project_id:
            await queue.send_log('agent', project_id, local_id, msg)
    await logger.complete()


def configure_logging():
    logger.add(rest_logsink,
               format="{message}", level="INFO")
