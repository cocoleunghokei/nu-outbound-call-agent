"""Environment-driven settings. Every field has a safe default so tests run without a .env."""

from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Backend settings, read from environment variables or backend/.env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)

    # Postgres password URL through the Supabase session pooler.
    database_url: str = ""

    supabase_url: str = ""
    supabase_jwt_issuer: str = ""
    supabase_jwks_url: str = ""
    # Legacy HS256 fallback.
    supabase_jwt_secret: SecretStr | None = None

    cors_origins: list[str] = ["http://localhost:5173"]
    # Regex for Vercel preview origins.
    cors_origin_regex: str | None = None

    vapi_api_key: SecretStr = SecretStr("")
    vapi_webhook_secret: SecretStr = SecretStr("")
    vapi_phone_number_id: str = ""
    vapi_base_url: str = "https://api.vapi.ai"
    vapi_ip_allowlist: bool = False

    voice_platform: Literal["vapi", "fake"] = "fake"

    # LLM for custom-goal briefs (PC-3, app/briefs/llm.py). An empty key turns the LLM off:
    # custom briefs then use the user's own words, and the app still starts.
    deepseek_api_key: SecretStr = SecretStr("")
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

    @model_validator(mode="after")
    def _derive_jwt_urls(self) -> "Settings":
        base = self.supabase_url.rstrip("/")
        if not self.supabase_jwt_issuer and base:
            self.supabase_jwt_issuer = f"{base}/auth/v1"
        if not self.supabase_jwks_url and base:
            self.supabase_jwks_url = f"{base}/auth/v1/.well-known/jwks.json"
        return self
