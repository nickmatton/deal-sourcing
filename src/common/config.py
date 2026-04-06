from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DB_")

    host: str = "localhost"
    port: int = 5432
    user: str = "deal_sourcing"
    password: str = "dev_password"
    name: str = "deal_sourcing"

    @property
    def url(self) -> str:
        return f"postgresql://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"


class RedisSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="REDIS_")

    host: str = "localhost"
    port: int = 6379
    db: int = 0

    @property
    def url(self) -> str:
        return f"redis://{self.host}:{self.port}/{self.db}"


class KafkaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KAFKA_")

    bootstrap_servers: str = "localhost:9092"


class MLflowSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MLFLOW_")

    tracking_uri: str = "http://localhost:5000"
    experiment_name: str = "deal-sourcing"


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LLM_")

    provider: str = "anthropic"
    model: str = "claude-sonnet-4-20250514"
    api_key: str = ""
    max_tokens: int = 4096


class PipelineSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    project_root: Path = Field(default_factory=lambda: Path(__file__).resolve().parent.parent.parent)
    environment: str = "development"
    log_level: str = "INFO"

    db: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    kafka: KafkaSettings = Field(default_factory=KafkaSettings)
    mlflow: MLflowSettings = Field(default_factory=MLflowSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)

    sell_probability_threshold: float = 0.3
    thesis_fit_score_threshold: float = 0.5
    irr_hurdle_rate: float = 0.20
    irr_priority_threshold: float = 0.25
    max_outreach_batch_size: int = 20
    illiquidity_discount_low: float = 0.15
    illiquidity_discount_high: float = 0.30


def get_settings() -> PipelineSettings:
    return PipelineSettings()
