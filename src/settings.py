from pydantic import BaseSettings


class AppSettings(BaseSettings):
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

    HUB_URL: str = 'http://localhost:5000'

    CYKUBE_APP_URL: str = 'http://localhost:4201'
    CYKUBE_MAIN_URL: str = 'http://localhost:5002'
    # CYKUBE_APP_URL: str = 'https://cypresskube.ddns.net'

    # Public face to NGINX server for screenshots and videos
    RESULT_URL: str = 'http://localhost:5000/results'
    DIST_URL: str = 'http://localhost:5000/dist-cache'
    CYPRESS_RUNNER_VERSION: str = '8.3.1-1.0'
    DIST_CACHE_TTL_HOURS: int = 365*24


settings = AppSettings()
