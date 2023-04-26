from pydantic import BaseSettings


class AgentSettings(BaseSettings):
    API_TOKEN: str = 'cykubeauth'

    K8: bool = True

    NAMESPACE = 'cykubed'
    PLATFORM: str = 'Minikube'

    SERVER_START_TIMEOUT: int = 60
    CYPRESS_RUN_TIMEOUT: int = 10*60

    # keep app distributions for 1 hr in case of reruns
    APP_DISTRIBUTION_CACHE_TTL: int = 3600
    # keep the node distributions for 30 days
    NODE_DISTRIBUTION_CACHE_TTL: int = 30*3600

    ENCODING = 'utf8'

    BUILD_TIMEOUT: int = 900
    NODE_PATH: str = None

    TEST = False

    MAX_HTTP_RETRIES = 10
    MAX_HTTP_BACKOFF = 60

    MESSAGE_POLL_PERIOD = 1

    AGENT_URL: str = 'http://127.0.0.1:5000'
    MAIN_API_URL: str = 'https://app.cykube.net/api'

    SENTRY_DSN: str = None

    HOSTNAME: str = None  # for testin

    STORAGE_CLASS = 'cykubed-storageclass'


settings = AgentSettings()
