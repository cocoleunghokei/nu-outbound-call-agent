"""Environment-driven settings. Every field has a safe default so tests run without a .env."""

from typing import Literal

from pydantic import SecretStr, field_validator, model_validator
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
    # This backend's public https origin (e.g. the Railway URL). Vapi sends webhooks to
    # {public_base_url}/webhooks/vapi; POST /calls refuses to dial a real call without it.
    public_base_url: str = ""

    voice_platform: Literal["vapi", "fake"] = "fake"

    # Numbers POST /calls may dial, as a JSON list like CORS_ORIGINS (e.g. ["+16175550142"]).
    # Empty means no restriction. A stopgap while the backend is reachable by anonymous
    # sessions and the per-user call caps (DC-15) don't exist yet: set it to the test phones.
    call_destination_allowlist: list[str] = []

    @field_validator("call_destination_allowlist")
    @classmethod
    def _normalise_allowlist(cls, entries: list[str]) -> list[str]:
        """Store every entry as +1XXXXXXXXXX, so the check matches the brief's normalised number."""
        # Imported here: app.briefs imports app.auth, which imports this module.
        from app.briefs.phone import check_phone

        normalised: list[str] = []
        for entry in entries:
            checked = check_phone(entry)
            if checked.e164 is None:
                raise ValueError(f"CALL_DESTINATION_ALLOWLIST entry {entry!r}: {checked.message}")
            normalised.append(checked.e164)
        return normalised

    # LLM for custom-goal briefs (PC-3, app/briefs/llm.py). An empty key turns the LLM off:
    # custom briefs then use the user's own words, and the app still starts.
    deepseek_api_key: SecretStr = SecretStr("")
    deepseek_base_url: str = "https://api.deepseek.com"
    # deepseek-chat (V3) was retired by DeepSeek on 2026-07-24; the team's key offers
    # deepseek-flash and deepseek-v4-pro.
    deepseek_model: str = "deepseek-flash"

    @model_validator(mode="after")
    def _derive_jwt_urls(self) -> "Settings":
        base = self.supabase_url.rstrip("/")
        if not self.supabase_jwt_issuer and base:
            self.supabase_jwt_issuer = f"{base}/auth/v1"
        if not self.supabase_jwks_url and base:
            self.supabase_jwks_url = f"{base}/auth/v1/.well-known/jwks.json"
        return self
