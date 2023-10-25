from typing import Optional

from pydantic_settings import BaseSettings


class AgentSettings(BaseSettings):
    API_TOKEN: str = 'cykubeauth'
    AGENT_VERSION: str = '1.0.0'

    K8: bool = True

    NAMESPACE: str = 'cykubed'
    PRIORITY_CLASS: str = 'cykubed-default-priority'
    PLATFORM: str = 'generic'
    VOLUME_SNAPSHOT_CLASS: Optional[str] = None

    FAKE_DATETIME: Optional[str] = None

    READ_ONLY_MANY: bool = True

    SERVER_START_TIMEOUT: int = 60
    CYPRESS_RUN_TIMEOUT: int = 10*60

    PORT: int = 9001

    # keep app distributions for 1 hr in case of reruns
    APP_DISTRIBUTION_CACHE_TTL: int = 1 * 3600
    # keep the node distributions for 7 days (TTL is reset on each use)
    NODE_DISTRIBUTION_CACHE_TTL: int = 7 * 3600

    ENCODING: str = 'utf8'

    TEST: bool = False

    MAX_HTTP_RETRIES: int = 10
    MAX_HTTP_BACKOFF: int = 60

    MESSAGE_POLL_PERIOD: int = 1

    MAIN_API_URL: str = 'https://api.cykubed.com'
    # clean up testrun state after this time period (after the runner deadline)
    TESTRUN_STATE_TTL: int = 30 * 3600 # reduce this when I go to production!
    JOB_TRACKER_PERIOD: int = 30

    SENTRY_DSN: Optional[str] = None

    HOSTNAME: Optional[str] = None  # for testing

    REDIS_SECRET_NAME: str = 'cykubed-agent-redis'
    STORAGE_CLASS: str = 'cykubed'

    @property
    def use_read_only_many(self) -> bool:
        return self.PLATFORM in ['minikube', 'gke'] and self.READ_ONLY_MANY


settings = AgentSettings()
