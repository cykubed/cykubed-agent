from celery import Celery

import jobs
from settings import settings

jobs.connect_k8()

app = Celery('tasks', broker=f'redis://{settings.REDIS_HOST}:6379/0')
