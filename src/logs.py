from __future__ import annotations

import loguru
from loguru import logger

from common.logupload import upload_logs


def rest_logsink(msg: loguru.Message):
    record = msg.record
    tr = record['extra'].get('tr')
    if tr:
        upload_logs(tr.project.id, tr.local_id, msg)
    else:
        local_id = record['extra'].get('local_id')
        project_id = record['extra'].get('project_id')
        if local_id and project_id:
            upload_logs(project_id, local_id, msg)


def configure_logging():
    logger.add(rest_logsink,
               format="{time:HH:mm:ss.SSS} {level} {message}", level="INFO")
