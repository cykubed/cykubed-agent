import enum
from functools import lru_cache
from typing import Iterator

from fastapi_utils.session import FastAPISessionMaker
from sqlalchemy import Column, Integer, String, ForeignKey, DateTime, Boolean, Enum
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


class PlatformEnum(enum.Enum):
    BITBUCKET = 'bitbucket'
    JIRA = 'jira'
    SLACK = 'slack'


class Project(Base):
    __tablename__ = 'project'
    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=True)
    url = Column(String(255), nullable=True)
    platform = Column(Enum(PlatformEnum))


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
    project_id = Column(Integer, ForeignKey('project.id'), nullable=False)
    project = relationship('Project', lazy='joined')

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

    @property
    def repos(self):
        return self.project.url


class OAuthToken(Base):
    __tablename__ = 'oauth_token'
    platform = Column(Enum(PlatformEnum), primary_key=True, unique=True)
    access_token = Column(String(1024))
    refresh_token = Column(String(1024))
    expiry = Column(DateTime)
    cloud_id = Column(String(1024), nullable=True)
