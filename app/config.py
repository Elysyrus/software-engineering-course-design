from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./course_registration.db"
    app_env: str = "development"
    # 成员 4：计费模拟服务地址与重试间隔（关闭事务已固定金额，重试只复用任务快照）
    billing_service_url: str = "http://127.0.0.1:8082"
    billing_retry_seconds: int = 30
    billing_request_timeout_seconds: float = 5.0

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()

