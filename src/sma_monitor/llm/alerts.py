"""Operational alerts for LLM backend failures."""
from __future__ import annotations

import logging
import os
import smtplib
import threading
from datetime import UTC, datetime
from email.mime.text import MIMEText

from ..config import settings

log = logging.getLogger("sma_monitor.llm.alerts")

_LOCK = threading.Lock()
_LAST_SENT: dict[str, float] = {}


def alert_codex_failure(
    *,
    stage: str | None,
    method: str,
    error: BaseException | str,
    fallback_label: str | None,
    fallback_succeeded: bool | None = None,
) -> bool:
    """Email the operator when Codex fails and the pipeline must fall back.

    Returns True only when an SMTP email was sent. Failures are logged and never
    raised into the LLM path.
    """
    subject = "AI CRO Codex LLM failure"
    stage_label = stage or "default"
    dedupe_key = f"{stage_label}:{method}:{type(error).__name__}:{fallback_label or 'none'}"
    if not _should_send(dedupe_key):
        return False
    if not _email_configured():
        log.warning(
            "codex_failure_alert_not_sent_smtp_missing",
            extra={"stage": stage_label, "method": method, "fallback": fallback_label},
        )
        return False

    body = "\n".join([
        "Codex GPT failed inside AI Chief Risk Officer.",
        "",
        f"Time UTC: {datetime.now(UTC).isoformat()}",
        f"Stage: {stage_label}",
        f"Method: {method}",
        f"Fallback model: {fallback_label or 'none'}",
        f"Fallback succeeded: {_fmt_bool(fallback_succeeded)}",
        "",
        "Codex error:",
        str(error)[:1200],
        "",
        "Action: check Codex login/subscription/token health on this host or VPS.",
    ])
    try:
        _send_email(subject=subject, body=body)
        log.warning(
            "codex_failure_alert_sent",
            extra={"stage": stage_label, "method": method, "fallback": fallback_label},
        )
        return True
    except Exception as e:  # noqa: BLE001 - alerting must not break fallback.
        log.error("codex_failure_alert_failed", extra={"err": str(e)[:300]})
        return False


def alert_codex_unavailable(*, stage: str | None, fallback_label: str | None) -> bool:
    return alert_codex_failure(
        stage=stage,
        method="provider_selection",
        error="Codex CLI is not available or Codex auth.json is missing.",
        fallback_label=fallback_label,
        fallback_succeeded=None,
    )


def _should_send(key: str) -> bool:
    try:
        cooldown = max(0, int(os.environ.get("SMA_CODEX_ALERT_COOLDOWN_S", "900")))
    except ValueError:
        cooldown = 900
    now = datetime.now(UTC).timestamp()
    with _LOCK:
        last = _LAST_SENT.get(key)
        if last is not None and now - last < cooldown:
            return False
        _LAST_SENT[key] = now
        return True


def _email_configured() -> bool:
    return all([
        settings.smtp_host,
        settings.smtp_username,
        settings.smtp_password,
        settings.alert_email_from,
        settings.alert_email_to,
    ])


def _send_email(*, subject: str, body: str) -> None:
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = settings.alert_email_from or ""
    msg["To"] = settings.alert_email_to or ""
    msg["Subject"] = subject
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:  # type: ignore[arg-type]
        smtp.starttls()
        smtp.login(settings.smtp_username, settings.smtp_password)  # type: ignore[arg-type]
        smtp.sendmail(settings.alert_email_from, [settings.alert_email_to], msg.as_string())  # type: ignore[list-item,arg-type]


def _fmt_bool(value: bool | None) -> str:
    if value is None:
        return "not attempted yet"
    return "yes" if value else "no"
