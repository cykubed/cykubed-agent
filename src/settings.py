from pydantic import BaseSettings


class AppSettings(BaseSettings):
    BITBUCKET_WEBHOOK_TOKEN: str = None
    TEST_RUN_TIMEOUT: int = 30 * 60
    SPEC_FILE_TIMEOUT: int = 5 * 60
    REDIS_HOST: str = 'localhost'
    CYPRESSHUB_DATABASE_URL: str = 'sqlite:///:memory:'
    BITBUCKET_APP_PASSWORD: str = 'dummy'
    BITBUCKET_USERNAME: str = 'nickbrook'
    ARTIFACTS_URL = 'https://storage.googleapis.com/kisanhub-cypress-artifacts'
    BUILD_TIMEOUT: int = 900
    NPM_CACHE_DIR = '/tmp/testhub/cache'
    DIST_DIR = '/tmp/testhub/dists'
    SLACK_TOKEN: str = None
    JIRA_TOKEN: str = None
    JIRA_USER: str = 'nick@kisanhub.com'
    TEST_MODE: bool = True


settings = AppSettings()
