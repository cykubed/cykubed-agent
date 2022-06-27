from pydantic import BaseSettings


class AppSettings(BaseSettings):
    API_TOKEN: str = 'cykubeauth'

    TEST_RUN_TIMEOUT: int = 30 * 60
    SPEC_FILE_TIMEOUT: int = 5 * 60
    LOG_UPDATE_PERIOD = 10

    BUILD_TIMEOUT: int = 900

    TEST_MODE: bool = True
    PARALLELISM: int = 1

    HUB_URL: str = 'http://127.0.0.1:5000'
    CACHE_URL: str = 'http://127.0.0.1:5020/cache'

    CYKUBE_APP_URL: str = 'http://localhost:5002'
    CACHE_DIR: str = 'cache'

    CYPRESS_RUNNER_VERSION: str = '8.3.1-1.0'
    DIST_CACHE_TTL_HOURS: int = 365*24


settings = AppSettings()
