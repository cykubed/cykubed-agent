from pydantic import BaseSettings


class AgentSettings(BaseSettings):
    API_TOKEN: str = 'cykubeauth'
    AGENT_VERSION: str = '1.0.0'

    K8: bool = True

    NAMESPACE = 'cykubed'
    PRIORITY_CLASS = 'cykubed-default-priority'
    PLATFORM: str = 'generic'
    VOLUME_SNAPSHOT_CLASS: str = None

    FAKE_DATETIME: str = None

    READ_ONLY_MANY: bool = True

    SERVER_START_TIMEOUT: int = 60
    CYPRESS_RUN_TIMEOUT: int = 10*60

    PORT = 9001

    # keep app distributions for 1 hr in case of reruns
    APP_DISTRIBUTION_CACHE_TTL: int = 1 * 3600
    # keep the node distributions for 7 days (TTL is reset on each use)
    NODE_DISTRIBUTION_CACHE_TTL: int = 7 * 3600

    ENCODING = 'utf8'

    TEST = False

    MAX_HTTP_RETRIES = 10
    MAX_HTTP_BACKOFF = 60

    MESSAGE_POLL_PERIOD = 1

    MAIN_API_URL: str = 'https://api.cykubed.com'
    # clean up testrun state after this time period (after the runner deadline)
    TESTRUN_STATE_TTL: int = 30 * 3600 # reduce this when I go to production!
    JOB_TRACKER_PERIOD: int = 30

    SENTRY_DSN: str = None

    HOSTNAME: str = None  # for testin

    LOCAL_REDIS: bool = True
    REDIS_SECRET_NAME = 'cykubed-agent-redis'
    STORAGE_CLASS = 'cykubed'

    @property
    def use_read_only_many(self):
        return self.PLATFORM in ['minikube', 'gke'] and self.READ_ONLY_MANY


settings = AgentSettings()
