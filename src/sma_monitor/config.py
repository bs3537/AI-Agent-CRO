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
    # W2: Brave replaces Exa as the primary search source; Semantic Scholar
    # provides literature (bucket #10, replacing Scite) and FMP adds financials
    # (#4/#7/#12 + dashboard). exa_api_key/scite_api_key are retained as legacy
    # fallbacks.
    exa_api_key: str | None = None
    brave_search_api_key: str | None = None
    scite_api_key: str | None = None
    # W2 (updated): Semantic Scholar is the live literature source for bucket
    # #10 — a plain REST key (x-api-key header), so no OAuth/logout on the host.
    semantic_scholar_api_key: str | None = None
    fmp_api_key: str | None = None
    # SEC EDGAR (financials primary, ahead of FMP) needs no key — only a
    # descriptive User-Agent per SEC's fair-access policy. Set a real contact.
    sec_edgar_user_agent: str = "sma-monitor (contact: admin@example.com)"
    # PubMed (NCBI E-utilities) is the biomed-literature primary; works without
    # a key (3 req/s). An optional NCBI_API_KEY raises the limit to 10 req/s.
    ncbi_api_key: str | None = None

    # Scoring & red team — Phase 3-4
    anthropic_api_key: str | None = None
    # OpenRouter fallback LLMs. Codex remains primary; these models are used
    # only when Codex is unavailable or a Codex call fails.
    openrouter_api_key: str | None = None
    openrouter_model: str = "xiaomi/mimo-v2.5-pro"
    openrouter_fallback_models: str = "minimax/minimax-m2.7"
    openrouter_file_model: str = "google/gemini-3.1-flash-lite"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

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
    # Cloud SQLite / Turso. When both values are set, db.connection() uses
    # Turso/libSQL; otherwise it falls back to local DATA_ROOT/sma.db.
    turso_database_url: str | None = None
    turso_auth_token: str | None = None

    # Return the list of secrets required by `phase` that aren't set. Treats
    # both unset (None) and present-but-empty ("") as missing, so a blank
    # `KEY=` line in .env skips the phase gracefully instead of crashing.
    # Bootstrap and each CLI uses this to skip work when creds are missing.
    def missing_for(self, phase: int) -> list[str]:
        required_by_phase: dict[int, list[str]] = {
            1: ["ibkr_flex_token", "ibkr_flex_query_id"],
            2: ["brave_search_api_key"],  # W2: Brave is the primary news source
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
        return [v for v in required_by_phase.get(phase, []) if not getattr(self, v)]


# Singleton settings instance imported by every phase via `from .config import settings`.
settings = Settings()
