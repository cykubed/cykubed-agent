from functools import lru_cache
from typing import Iterator

from fastapi_utils.session import FastAPISessionMaker
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session
from sqlalchemy.orm import relationship

from settings import settings

Base = declarative_base()

BITBUCKET_PLATFORM = 1


def get_db() -> Iterator[Session]:
    """ FastAPI dependency that provides a sqlalchemy session """
    yield from _get_fastapi_sessionmaker().get_db()


@lru_cache()
def _get_fastapi_sessionmaker() -> FastAPISessionMaker:
    """ This function could be replaced with a global variable if preferred """
    return FastAPISessionMaker(settings.CYPRESSHUB_DATABASE_URL)


class SpecFile(Base):
    __tablename__ = 'spec_file'

    id = Column(Integer, primary_key=True)
    file = Column(String(255))
    testrun = relationship('TestRun', back_populates='files')
    testrun_id = Column(Integer, ForeignKey('test_run.id'), nullable=False)
    started = Column(DateTime, nullable=True)
    finished = Column(DateTime, nullable=True)


class TestRun(Base):
    __tablename__ = 'test_run'

    STATES = [
        ('building', 'Building'),
        ('running', 'Running'),
        ('timeout', 'Timeout'),
        ('passed', 'Passed'),
        ('failed', 'Failed'),
        ('cancelled', 'Cancelled')
    ]

    id = Column(Integer, primary_key=True)

    repos = Column(String(1024))
    started = Column(DateTime)
    finished = Column(DateTime, nullable=True)
    sha = Column(String(64))
    branch = Column(String(128))
    active = Column(Boolean(), default=True)
    status = Column(String(64))
    files = relationship('SpecFile', back_populates='testrun')


sessionmaker = FastAPISessionMaker(settings.CYPRESSHUB_DATABASE_URL)
