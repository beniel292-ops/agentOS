import base64
import uuid
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_ENV, extra="ignore", env_ignore_empty=True
    )

    database_url: str = "postgresql+psycopg://agentos:agentos@localhost:5434/agentos"
    redis_url: str = "redis://localhost:6380/0"
    omniroute_base_url: str = "http://localhost:20128/v1"
    omniroute_api_key: str = ""
    ai_model: str = "agy/gemini-3.8-flash-high"
    ai_fallback_models: list[str] = ["kr/claude-haiku-4.5", "agy/gemini-3.8-flash-medium"]
    ai_timeout_seconds: float = 60
    clerk_publishable_key: str = Field(
        default="", validation_alias="NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY"
    )
    clerk_authorized_parties: list[str] = ["http://localhost:3000"]
    # Telegram approvals + Chief of Staff bot. Empty token = all Telegram features off.
    telegram_approvals_bot_token: str = ""
    telegram_owner_user_id: int | None = None  # private chat id == user id
    telegram_owner_tenant_id: uuid.UUID | None = None
    web_app_url: str = "http://localhost:3000"
    # OpenClaw inbound webhook: approved voice-call requests are handed to Jarvis here.
    openclaw_hooks_url: str = "http://127.0.0.1:18789/hooks/agent"
    openclaw_hooks_token: str = ""
    # Voice (agentring): the worker places calls, the MCP server gives the voice AI its tools.
    agentring_bin: str = (
        "/Users/user/Documents/Archive/Desktop/agentring/daemon/.venv/bin/agentring"
    )
    # The MCP server has no auth header support on the agentring side, so the secret is the URL
    # path: /mcp/<token>. Every other path is a 404. The server refuses to start without it.
    voice_mcp_token: str = ""
    voice_mcp_url: str = "http://127.0.0.1:8787/mcp"  # derived below; a full override must
    # contain the token
    voice_provider: str = "local"
    voice_agent_key: str = ""  # scoped agent key used only by the MCP server
    # not AGENTOS_API_URL: .env already uses that name for the Docker agents
    agentos_api_url: str = Field(
        default="http://127.0.0.1:8000", validation_alias="VOICE_AGENTOS_API_URL"
    )

    # Google Drive (read-only, owner's tenant only). Files are git-ignored, under secrets/.
    google_oauth_client_path: Path = ROOT_ENV.parent / "secrets" / "google-oauth-client.json"
    google_drive_token_path: Path = ROOT_ENV.parent / "secrets" / "google-drive-token.json"

    @property
    def telegram_enabled(self) -> bool:
        return bool(
            self.telegram_approvals_bot_token
            and self.telegram_owner_user_id
            and self.telegram_owner_tenant_id
        )

    @model_validator(mode="after")
    def _mcp_url_has_token(self):
        if self.voice_mcp_token and self.voice_mcp_token not in self.voice_mcp_url:
            origin = self.voice_mcp_url.split("/mcp")[0].rstrip("/")
            self.voice_mcp_url = f"{origin}/mcp/{self.voice_mcp_token}"
        return self

    @field_validator("google_oauth_client_path", "google_drive_token_path")
    @classmethod
    def _from_root(cls, v: Path) -> Path:
        return v if v.is_absolute() else ROOT_ENV.parent / v

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
