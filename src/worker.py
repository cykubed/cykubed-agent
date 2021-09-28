import os

from celery import Celery

import jobs
from settings import settings

jobs.connect_k8()

app = Celery('tasks', broker=f'redis://{settings.REDIS_HOST}:6379/0')


os.makedirs(settings.DIST_DIR, exist_ok=True)
os.makedirs(settings.NPM_CACHE_DIR, exist_ok=True)
os.makedirs(settings.RESULTS_DIR, exist_ok=True)
