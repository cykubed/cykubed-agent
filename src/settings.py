from pydantic import BaseSettings

#
# Most of this needs to move to the database
#

class AppSettings(BaseSettings):
    BITBUCKET_WEBHOOK_TOKEN: str = None
    TEST_RUN_TIMEOUT: int = 30 * 60
    SPEC_FILE_TIMEOUT: int = 5 * 60
    REDIS_HOST: str = 'localhost'
    CYPRESSHUB_DATABASE_URL: str = 'sqlite:///:memory:'
    BITBUCKET_APP_PASSWORD: str = 'dummy'
    BITBUCKET_USERNAME: str = 'nickbrook'

    FIREBASE_PROJECT_ID: str = 'kisanhub-uat'

    BUILD_TIMEOUT: int = 900
    NPM_CACHE_DIR = '/var/lib/cypresshub/npm-cache'
    DIST_DIR = '/var/lib/cypresshub/dist-cache'
    RESULTS_DIR = '/var/lib/cypresshub/results'
    SLACK_TOKEN: str = None
    JIRA_TOKEN: str = None
    JIRA_USER: str = 'nick@kisanhub.com'
    TEST_MODE: bool = True
    PARALLELISM: int = 1

    RESULTS_UI_URL: str = 'http://cypresshub.kisanhub.com'
    HUB_URL: str = 'http://cypresshub:5000'
    # Public face to NGINX server for screenshots and videos
    RESULT_URL: str = 'http://192.168.49.2:32600/results'
    DIST_URL: str = 'http://cypresshub-external:5001/dist-cache'
    CYPRESS_RUNNER_VERSION: str = '8.3.1-1.0'
    DIST_CACHE_TTL_HOURS: int = 365*24


settings = AppSettings()
