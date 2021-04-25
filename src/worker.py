from celery import Celery

from settings import settings


app = Celery('tasks', broker=f'redis://{settings.REDIS_HOST}:6379/0')
