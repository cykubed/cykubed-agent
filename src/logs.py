from __future__ import annotations
import loguru
from loguru import logger

from common.logupload import upload_logs


def rest_logsink(msg: loguru.Message):
    record = msg.record
    trid = record['extra'].get('trid')
    if trid:
        upload_logs(trid, msg)


def configure_logging():
    logger.add(rest_logsink,
               format="{time:HH:mm:ss.SSS} {level} {message}", level="INFO")


if __name__ == "__main__":
    configure_logging()
    logger.info('test', trid=19)
    logger.error('fish')
