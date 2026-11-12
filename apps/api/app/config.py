import base64
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_ENV, extra="ignore")

    database_url: str = "postgresql+psycopg://agentos:agentos@localhost:5434/agentos"
    redis_url: str = "redis://localhost:6380/0"
    clerk_publishable_key: str = Field(
        default="", validation_alias="NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY"
    )
    clerk_authorized_parties: list[str] = ["http://localhost:3000"]

    @property
    def clerk_issuer(self) -> str:
        """Frontend API URL, encoded in the publishable key (pk_test_<base64(host$)>)."""
        encoded = self.clerk_publishable_key.split("_", 2)[-1]
        if not encoded:
            return ""
        host = base64.b64decode(encoded + "=" * (-len(encoded) % 4)).decode().rstrip("$")
        return f"https://{host}"

    @property
    def clerk_jwks_url(self) -> str:
        return f"{self.clerk_issuer}/.well-known/jwks.json"


settings = Settings()
