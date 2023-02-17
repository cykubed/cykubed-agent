import asyncio
from asyncio import QueueFull
from functools import lru_cache

import httpx
from loguru import logger

from common import schemas
from common.settings import settings
from common.utils import get_headers


def post_testrun_status(tr: schemas.NewTestRun, status: str):
    update_status(tr.project.id, tr.local_id, status)


@lru_cache(maxsize=10000)
def update_status(project_id: int, local_id: int, status: str):
    resp = httpx.put(f'{settings.MAIN_API_URL}/agent/testrun/{project_id}/{local_id}/status/{status}',
                     headers=get_headers())
    if resp.status_code != 200:
        raise Exception(f"Failed to update status for run {local_id} on project {project_id}")


class MessageQueue:
    """
    Queue for messages to be sent to cykube via the websocket
    """
    async def init(self):
        """
        We can't do this in the constructor as it needs to be inside an event loop
        (and it's easier if we use the singleton pattern)
        """
        self.queue = asyncio.Queue(maxsize=1000)

    def add_agent_msg(self, msg: schemas.AgentEvent):
        try:
            self.queue.put_nowait(msg.json())
        except QueueFull:
            logger.error("Log message queue full - dropping message")

    def send_log(self, source: str, testrun_id: int, msg):
        """
        Send a log event
        :param testrun_id:
        :param source:
        :param msg:
        :return:
        """
        item = schemas.AgentLogMessage(ts=msg.record['time'],
                                       testrun_id=testrun_id,
                                       level=msg.record['level'].name.lower(),
                                       msg=msg,
                                       source=source)
        self.add_agent_msg(item)

    async def get(self):
        """
        Return the next item in the queue
        :return:
        """
        return await self.queue.get()

    def task_done(self):
        """
        Called by the websocket client when the task has been sent
        :return:
        """
        self.queue.task_done()


queue = MessageQueue()
