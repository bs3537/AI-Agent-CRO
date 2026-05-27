"""Delivery channels.

Channels are stateless objects; the pipeline instantiates and calls them.
FileChannel is always-on (archive + offline review). StdoutChannel is the
testing default. EmailChannel only when SMTP creds are configured.

Adding Slack/SMS later: subclass Channel with send_alert / send_digest
implementations and register it in build_channels().
"""
from __future__ import annotations

import logging
import smtplib
import sys
from abc import ABC, abstractmethod
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from ..config import settings
from ..paths import DIGESTS_DIR

log = logging.getLogger("sma_monitor.outputs.channels")

# Subdirectory of digests/ used by FileChannel for the per-day alert archive.
ALERTS_DIR = DIGESTS_DIR / "alerts"
# Subdirectory of digests/ used by FileChannel for the morning thesis email
# archive — kept separate so it never overwrites the evening digest file.
THESIS_DIR = DIGESTS_DIR / "thesis"


# Abstract base for every delivery channel. Subclasses implement send_alert
# (per-event) and send_digest (per-day). Channels are stateless.
class Channel(ABC):
    name: str

    @abstractmethod
    def send_alert(self, ticker: str, rendered_text: str) -> None: ...

    @abstractmethod
    def send_digest(self, date_iso: str, rendered_md: str) -> None: ...

    # Send the morning thesis-drift email (W7). Concrete no-op default so this
    # is an optional capability — the abstract contract stays alert + digest.
    def send_thesis_email(self, date_iso: str, subject: str, rendered_md: str):
        return None


# Always-on channel that appends alerts to a per-day markdown file and
# writes the digest as one file per day. Used as the offline review trail.
class FileChannel(Channel):
    """Append alerts to per-day file; write digest as one file per day."""

    name = "file"

    # Append the rendered alert text to data/digests/alerts/YYYY-MM-DD.md.
    def send_alert(self, ticker: str, rendered_text: str) -> None:
        ALERTS_DIR.mkdir(parents=True, exist_ok=True)
        from datetime import datetime, timezone
        date_iso = datetime.now(timezone.utc).date().isoformat()
        p = ALERTS_DIR / f"{date_iso}.md"
        sep = "\n\n---\n\n" if p.exists() else ""
        with p.open("a") as f:
            f.write(f"{sep}{rendered_text}\n")

    # Write the digest markdown to data/digests/YYYY-MM-DD.md. Overwrites
    # any prior digest for that date so re-runs produce one canonical file.
    def send_digest(self, date_iso: str, rendered_md: str) -> Path:
        DIGESTS_DIR.mkdir(parents=True, exist_ok=True)
        p = DIGESTS_DIR / f"{date_iso}.md"
        with p.open("w") as f:
            f.write(rendered_md)
        return p

    # Archive the morning thesis email to data/digests/thesis/YYYY-MM-DD.md
    # (one canonical file per day, separate from the evening digest).
    def send_thesis_email(self, date_iso: str, subject: str, rendered_md: str) -> Path:
        THESIS_DIR.mkdir(parents=True, exist_ok=True)
        p = THESIS_DIR / f"{date_iso}.md"
        with p.open("w") as f:
            f.write(rendered_md)
        return p


# Channel that prints to stdout. Useful for testing and for tee'ing the
# digest to a terminal when invoked manually.
class StdoutChannel(Channel):
    name = "stdout"

    def send_alert(self, ticker: str, rendered_text: str) -> None:
        print(rendered_text, file=sys.stdout, flush=True)

    def send_digest(self, date_iso: str, rendered_md: str) -> None:
        print(rendered_md, file=sys.stdout, flush=True)

    def send_thesis_email(self, date_iso: str, subject: str, rendered_md: str) -> None:
        print(rendered_md, file=sys.stdout, flush=True)


# SMTP-over-STARTTLS email channel. Only configured when every SMTP_*
# credential is present. Plain-text body (no HTML) so it renders well on
# every mail client including mobile.
class EmailChannel(Channel):
    """SMTP STARTTLS. Only configured when SMTP_* creds are present."""

    name = "email"

    # Capture SMTP credentials + addresses at init so send_* can be called
    # without re-reading settings each time.
    def __init__(
        self,
        *,
        host: str,
        port: int,
        username: str,
        password: str,
        from_addr: str,
        to_addr: str,
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.from_addr = from_addr
        self.to_addr = to_addr

    # Send one alert as a separate email. Subject derived from the title
    # line of the rendered alert (first line, truncated to 120 chars).
    def send_alert(self, ticker: str, rendered_text: str) -> None:
        subject = rendered_text.split("\n", 1)[0][:120]
        self._send(subject=subject, text_body=rendered_text)

    # Send the digest as one email with a stable subject.
    def send_digest(self, date_iso: str, rendered_md: str) -> None:
        self._send(subject=f"SMA digest — {date_iso}", text_body=rendered_md)

    # Send the morning thesis-drift email with the caller-supplied subject.
    def send_thesis_email(self, date_iso: str, subject: str, rendered_md: str) -> None:
        self._send(subject=subject, text_body=rendered_md)

    # Internal SMTP helper used by both alert and digest paths. Raises so
    # the pipeline can log + record channel-send failures.
    def _send(self, *, subject: str, text_body: str) -> None:
        msg = MIMEMultipart("alternative")
        msg["From"] = self.from_addr
        msg["To"] = self.to_addr
        msg["Subject"] = subject
        msg.attach(MIMEText(text_body, "plain", "utf-8"))
        try:
            with smtplib.SMTP(self.host, self.port, timeout=30) as smtp:
                smtp.starttls()
                smtp.login(self.username, self.password)
                smtp.sendmail(self.from_addr, [self.to_addr], msg.as_string())
        except Exception as e:
            log.error("email_send_failed", extra={"err": str(e)})
            raise


# Build the default channel set. FileChannel is always included; add
# StdoutChannel when prefer_stdout; add EmailChannel when SMTP creds are
# all present. Add Slack/SMS by registering another Channel subclass here.
def build_channels(*, prefer_stdout: bool = False) -> list[Channel]:
    """Default set. FileChannel is always included (archive)."""
    channels: list[Channel] = [FileChannel()]
    if prefer_stdout:
        channels.append(StdoutChannel())
    if all([settings.smtp_host, settings.smtp_username, settings.smtp_password,
            settings.alert_email_from, settings.alert_email_to]):
        channels.append(EmailChannel(
            host=settings.smtp_host,  # type: ignore[arg-type]
            port=settings.smtp_port,
            username=settings.smtp_username,  # type: ignore[arg-type]
            password=settings.smtp_password,  # type: ignore[arg-type]
            from_addr=settings.alert_email_from,  # type: ignore[arg-type]
            to_addr=settings.alert_email_to,  # type: ignore[arg-type]
        ))
    return channels
