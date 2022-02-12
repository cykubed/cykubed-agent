from functools import lru_cache
from typing import Iterator

from fastapi_utils.session import FastAPISessionMaker
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session
from sqlalchemy.orm import relationship

from settings import settings

Base = declarative_base()


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
    # results = relationship('SpecFileResults', back_populates='results')
    testrun_id = Column(Integer, ForeignKey('test_run.id'), nullable=False)
    started = Column(DateTime, nullable=True)
    finished = Column(DateTime, nullable=True)


# class SpecFileResults(Base):
#     file_id = Column(Integer, ForeignKey('spec_file.id'), nullable=False)
#     data = Column(JSON, nullable=True)


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
    repos = Column(String(255))
    started = Column(DateTime)
    finished = Column(DateTime, nullable=True)
    sha = Column(String(64))
    branch = Column(String(128))
    active = Column(Boolean(), default=True)
    status = Column(String(64))
    files = relationship('SpecFile', back_populates='testrun')

    commit_summary = Column(String(255))
    commit_link = Column(String(255))
    pull_request_link = Column(String(255))

    avatar = Column(String(255))
    author = Column(String(64))
    author_slack_id = Column(String(255))
    jira_ticket = Column(String(255))


class SettingsModel(Base):
    __tablename__ = 'settings'

    hub_url = Column(String(255), nullable=True)
    bitbucket_url = Column(String(32), nullable=True)
    bitbucket_username = Column(String(32), nullable=True)
    bitbucket_password = Column(String(32), nullable=True)
    slack_token = Column(String(255), nullable=True)
    jira_url = Column(String(255), nullable=True)
    jira_user = Column(String(32), nullable=True)
    jira_token = Column(String(64), nullable=True)
