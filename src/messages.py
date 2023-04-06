import asyncio
from asyncio import QueueFull

from loguru import logger

from common import schemas
from common.enums import AgentEventType
from common.schemas import AppLogMessage


class MessageQueue:
    """
    Queue for messages to be sent to cykube via the websocket
    """

    async def init(self):
        """
        We can't do this in the constructor as it needs to be inside an event loop
        (and it's easier if we use the singleton pattern)
        """
        self.queue = asyncio.Queue(maxsize=2000)

    def add(self, msg: str):
        """
        Add a string message
        :param msg: serialised message
        """
        try:
            self.queue.put_nowait(msg)
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
        item = schemas.AgentLogMessage(testrun_id=testrun_id,
                                       type=AgentEventType.log,
                                       msg=AppLogMessage(ts=msg.record['time'],
                                                         level=msg.record['level'].name.lower(),
                                                         msg=msg,
                                                         source=source))
        self.add(item.json())

    async def get(self):
        """
        Return the next item in the queue
        :return:
        """
        return await self.queue.get()

    def task_done(self):
        """
        Called by the websocket async_client when the task has been sent
        :return:
        """
        self.queue.task_done()


queue = MessageQueue()
