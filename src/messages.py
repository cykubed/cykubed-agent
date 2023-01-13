import asyncio

from common import schemas


class MessageQueue:

    async def init(self):
        self.queue = asyncio.Queue(maxsize=1000)

    async def send_log(self, source: str, project_id: int, local_id: int, msg):
        item = schemas.AgentLogMessage(ts=msg.record['time'],
                                       project_id=project_id,
                                       local_id=local_id,
                                       level=msg.record['level'].name.lower(),
                                       msg=msg,
                                       source=source).json()
        await self.queue.put(item)

    async def send_status_update(self, project_id: int, local_id: int, status: schemas.TestRunStatus):
        await self.queue.put(schemas.AgentStatusMessage(
            project_id=project_id,
            local_id=local_id,
            status=status).json())

    async def get(self):
        return await self.queue.get()

    def task_done(self):
        self.queue.task_done()


queue = MessageQueue()
