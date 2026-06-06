from pydantic import AliasChoices, Field
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
        env_ignore_empty=True,
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
    # Codex CLI is the only live LLM backend. When it is unavailable, the
    # pipeline uses the deterministic heuristic fallbacks.

    # Alerts — Phase 5
    alert_email_from: str | None = None
    alert_email_to: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None

    # Resend transactional email (W7 morning risk brief + all alert/digest mail).
    # When resend_api_key is set it supersedes SMTP across every channel. The
    # sender must be a verified Resend domain in production; onboarding@resend.dev
    # works for first-run testing to the account owner's own address only.
    # resend_email_to is a comma-separated recipient list.
    resend_api_key: str | None = None
    resend_email_from: str | None = "AI CRO Risk Desk <onboarding@resend.dev>"
    resend_email_to: str | None = "bhavn008@gmail.com,bhavya20051@gmail.com"

    # Runtime
    # combined = local/dev legacy behavior; dashboard = Replit UI/API only;
    # runner = Hermes/VPS CLI jobs, Codex, and email delivery.
    sma_deployment_role: str = Field(
        "combined",
        validation_alias=AliasChoices(
            "SMA_DEPLOYMENT_ROLE",
            "SMA-DEPLOYMENT-ROLE",
            "sma_deployment_role",
        ),
    )
    data_root: str = Field(
        "./data",
        validation_alias=AliasChoices("DATA_ROOT", "DATA-ROOT", "data_root"),
    )
    log_level: str = Field(
        "INFO",
        validation_alias=AliasChoices("LOG_LEVEL", "LOG-LEVEL", "log_level"),
    )
    # Cloud SQLite / Turso. Runtime DB access requires both values. Tests use
    # an explicit test-only SQLite sandbox flag in db.py.
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
        }
        # Phase 5 (delivery) is satisfied by either transport. Resend wins when
        # its key is present (only the sender is mandatory — recipients default);
        # otherwise fall back to the legacy SMTP credential set.
        if phase == 5:
            return self._missing_delivery()
        return [v for v in required_by_phase.get(phase, []) if not getattr(self, v)]

    # Return the missing email-delivery secrets, honoring Resend-over-SMTP.
    def _missing_delivery(self) -> list[str]:
        if self.resend_api_key:
            return [
                v for v in ("resend_api_key", "resend_email_from", "resend_email_to")
                if not getattr(self, v)
            ]
        return [
            v for v in ("alert_email_from", "alert_email_to", "smtp_host",
                        "smtp_username", "smtp_password")
            if not getattr(self, v)
        ]

    def deployment_role(self) -> str:
        role = (self.sma_deployment_role or "combined").strip().lower()
        return role if role in {"combined", "dashboard", "runner"} else "combined"

    def is_dashboard_role(self) -> bool:
        return self.deployment_role() == "dashboard"

    def is_combined_role(self) -> bool:
        return self.deployment_role() == "combined"


# Singleton settings instance imported by every phase via `from .config import settings`.
settings = Settings()
