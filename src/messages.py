import asyncio

from common import schemas


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

    async def send_log(self, source: str, project_id: int, local_id: int, msg):
        """
        Send a log event
        :param source:
        :param project_id:
        :param local_id:
        :param msg:
        :return:
        """
        item = schemas.AgentLogMessage(ts=msg.record['time'],
                                       project_id=project_id,
                                       local_id=local_id,
                                       level=msg.record['level'].name.lower(),
                                       msg=msg,
                                       source=source).json()
        await self.queue.put(item)

    async def send_status_update(self, project_id: int, local_id: int,
                                 status: schemas.TestRunStatus):
        """
        Send a test run status update
        :param project_id:
        :param local_id:
        :param status:
        :return:
        """
        await self.queue.put(schemas.AgentStatusMessage(
            project_id=project_id,
            local_id=local_id,
            status=status).json())

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
