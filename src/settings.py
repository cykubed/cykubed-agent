from pydantic import BaseSettings


class AppSettings(BaseSettings):
    API_TOKEN: str = 'cykubeauth'

    TEST_RUN_TIMEOUT: int = 30 * 60
    SPEC_FILE_TIMEOUT: int = 5 * 60
    LOG_UPDATE_PERIOD = 10

    BUILD_TIMEOUT: int = 900

    TEST_MODE: bool = True
    PARALLELISM: int = 1

    HUB_URL: str = 'http://localhost:5000'

    CYKUBE_MAIN_URL = 'http://localhost:5002'
    CYKUBE_APP_URL: str = 'https://cykube.pagekite.me'

    CYPRESS_RUNNER_VERSION: str = '8.3.1-1.0'
    DIST_CACHE_TTL_HOURS: int = 365*24


settings = AppSettings()
