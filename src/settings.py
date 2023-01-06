import enum

from pydantic import BaseSettings


class JobMode(str, enum.Enum):
    K8 = 'k8'
    inline = 'inline'
    thread = 'thread'


class AppSettings(BaseSettings):
    API_TOKEN: str = 'cykubeauth'

    JOB_MODE: JobMode = 'k8'

    NAMESPACE = 'cykube'

    TEST_RUN_TIMEOUT: int = 30 * 60
    SPEC_FILE_TIMEOUT: int = 5 * 60

    JOB_TTL = 60*30
    LOG_UPDATE_PERIOD = 2

    BUILD_TIMEOUT: int = 900

    TEST_MODE: bool = True
    PARALLELISM: int = 1

    AGENT_URL: str = 'http://127.0.0.1:5000'
    CACHE_URL: str = 'http://127.0.0.1:5001'

    MAIN_API_URL: str = 'https://app.cykube.net/api'
    CACHE_DIR: str = '/var/lib/cykubecache'

    CYPRESS_RUNNER_VERSION: str = 'latest'
    DIST_CACHE_TTL_HOURS: int = 365*24


settings = AppSettings()
