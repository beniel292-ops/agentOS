from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_ENV, extra="ignore")

    database_url: str = "postgresql+psycopg://agentos:agentos@localhost:5434/agentos"
    redis_url: str = "redis://localhost:6380/0"


settings = Settings()
