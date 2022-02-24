from pydantic import BaseSettings

#
# Most of this needs to move to the database
#


class AppSettings(BaseSettings):
    API_TOKEN: str = 'cykubeauth'

    TEST_RUN_TIMEOUT: int = 30 * 60
    SPEC_FILE_TIMEOUT: int = 5 * 60
    REDIS_HOST: str = 'localhost'
    CYPRESSHUB_DATABASE_URL: str = 'sqlite:///:memory:'

    BITBUCKET_CLIENT_ID = '9ePNXdCRCdpcUjf4nz'
    BITBUCKET_SECRET = 'fAxQcde2WEqGZL3Gjsda9HTn2GrWvsJh'

    JIRA_CLIENT_ID = '2tR4DvNoAQejskUo6CeW0NKs95kkiDFm'
    JIRA_SECRET = 'ClBhi7ln8BcliVNxinZ2-Hp7bDM5fR3Et3MyOhMI2uKGbEZ20ZwnAVnzPuph-ajn'

    SLACK_CLIENT_ID = '3153511369013.3158744655460'
    SLACK_SECRET = '068e3008911d00d7ba83c3751feb72c0'

    BUILD_TIMEOUT: int = 900
    NPM_CACHE_DIR = '/var/lib/cypresshub/npm-cache'
    DIST_DIR = '/var/lib/cypresshub/dist-cache'
    RESULTS_DIR = '/var/lib/cypresshub/results'

    TEST_MODE: bool = True
    PARALLELISM: int = 1

    HUB_URL: str = 'http://localhost:5000'

    # CYKUBE_APP_URL: str = 'http://localhost:4201'
    CYKUBE_APP_URL: str = 'https://cypresskube.ddns.net'
    # Public face to NGINX server for screenshots and videos
    RESULT_URL: str = 'http://192.168.49.2:32600/results'
    DIST_URL: str = 'http://cypresshub-external:5001/dist-cache'
    CYPRESS_RUNNER_VERSION: str = '8.3.1-1.0'
    DIST_CACHE_TTL_HOURS: int = 365*24


settings = AppSettings()

