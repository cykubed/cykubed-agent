from pydantic import BaseSettings

#
# Most of this needs to move to the database
#

cached_settings = {}


class AppSettings(BaseSettings):
    BITBUCKET_WEBHOOK_TOKEN: str = None
    API_TOKEN: str = 'cykubeauth'

    TEST_RUN_TIMEOUT: int = 30 * 60
    SPEC_FILE_TIMEOUT: int = 5 * 60
    REDIS_HOST: str = 'localhost'
    CYPRESSHUB_DATABASE_URL: str = 'sqlite:///:memory:'

    BUILD_TIMEOUT: int = 900
    NPM_CACHE_DIR = '/var/lib/cypresshub/npm-cache'
    DIST_DIR = '/var/lib/cypresshub/dist-cache'
    RESULTS_DIR = '/var/lib/cypresshub/results'

    TEST_MODE: bool = True
    PARALLELISM: int = 1

    RESULTS_UI_URL: str = 'http://cypresshub.kisanhub.com'
    # Public face to NGINX server for screenshots and videos
    RESULT_URL: str = 'http://192.168.49.2:32600/results'
    DIST_URL: str = 'http://cypresshub-external:5001/dist-cache'
    CYPRESS_RUNNER_VERSION: str = '8.3.1-1.0'
    DIST_CACHE_TTL_HOURS: int = 365*24

    #
    # This will be fetched from the database
    #
    HUB_URL: str
    BITBUCKET_URL: str
    BITBUCKET_USERNAME: str
    BITBUCKET_PASSWORD: str
    SLACK_TOKEN: str
    JIRA_URL: str
    JIRA_USER: str
    JIRA_TOKEN: str

    @property
    def jira_auth(self):
        return self.JIRA_USER, self.JIRA_TOKEN

    @property
    def slack_headers(self):
        return {'Authorization': f'Bearer {self.SLACK_TOKEN}',
                'Content-Type': 'application/json; charset=utf8'}

    @property
    def bitbucket_auth(self):
        return settings.BITBUCKET_USERNAME, settings.BITBUCKET_APP_PASSWORD

    def __getattr__(self, key):
        if cached_settings and cached_settings.get(key.lower()):
            return cached_settings[key.lower()]
        return self[key]


settings = AppSettings()

