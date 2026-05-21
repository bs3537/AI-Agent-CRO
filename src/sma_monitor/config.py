from pydantic_settings import BaseSettings, SettingsConfigDict


# Project-wide settings loaded from .env via pydantic-settings.
# Every phase consults this object for its credentials. Settings are
# all Optional so the bootstrap can run with partial config and report
# per-phase readiness instead of crashing on import.
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # IBKR Flex Query — Phase 1
    ibkr_flex_token: str | None = None
    ibkr_flex_query_id: str | None = None

    # News ingestion — Phase 2
    exa_api_key: str | None = None

    # Scoring & red team — Phase 3-4
    anthropic_api_key: str | None = None

    # Alerts — Phase 5
    alert_email_from: str | None = None
    alert_email_to: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None

    # Runtime
    data_root: str = "./data"
    log_level: str = "INFO"

    # Return the list of secrets required by `phase` that aren't set.
    # Bootstrap and each CLI uses this to skip work gracefully when
    # credentials are missing rather than crashing.
    def missing_for(self, phase: int) -> list[str]:
        required_by_phase: dict[int, list[str]] = {
            1: ["ibkr_flex_token", "ibkr_flex_query_id"],
            2: ["exa_api_key"],
            3: ["anthropic_api_key"],
            4: ["anthropic_api_key"],
            5: [
                "alert_email_from",
                "alert_email_to",
                "smtp_host",
                "smtp_username",
                "smtp_password",
            ],
        }
        return [v for v in required_by_phase.get(phase, []) if getattr(self, v) is None]


# Singleton settings instance imported by every phase via `from .config import settings`.
settings = Settings()
