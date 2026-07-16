"""Browser-backed TipRanks forecast-page client and parser."""
from __future__ import annotations

import logging
import re
import shlex
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import quote

import httpx

log = logging.getLogger(__name__)

# TipRanks forecast-page defaults and text patterns used by the rendered-page parser.
TIPRANKS_BASE_URL = "https://www.tipranks.com/stocks"
DEFAULT_BROWSER_URL = "http://127.0.0.1:9377"
DEFAULT_BROWSER_COMMAND = "node /opt/hermes/node_modules/@askjo/camofox-browser/server.js"
NO_COVERAGE_PATTERNS = (
    "no analyst ratings",
    "no analyst consensus",
    "no price target available",
    "does not have sufficient analyst coverage",
    "not enough analyst coverage",
)
EMPTY_FORECAST_PATTERNS = (
    re.compile(r"based on\s+nan\s+analysts?", re.IGNORECASE),
    re.compile(
        r"average price target\s+(?:is\s+)?[―—-](?=\s|[.,;:]|$)",
        re.IGNORECASE,
    ),
    re.compile(
        r"stock 12 month forecast.{0,300}currently,?\s+no data available",
        re.IGNORECASE,
    ),
    re.compile(
        r"average analyst price target.{0,100}\s+is\s+[―—-](?=\s|[.,;:]|$)",
        re.IGNORECASE,
    ),
    re.compile(
        r"get in the past 3 months is\s+[―—-](?=\s|[.,;:]|$)",
        re.IGNORECASE,
    ),
    re.compile(r"(?:page not found|we couldn't find the page)", re.IGNORECASE),
)
COUNT_PATTERNS = (
    re.compile(r"based on\s+(\d+)\s+wall street analysts?", re.IGNORECASE),
    re.compile(r"(\d+)\s+wall street analysts?\s+offering", re.IGNORECASE),
)
MEAN_PATTERNS = (
    re.compile(
        r"average price target(?:\s+for\s+[^.]{1,100})?\s+is\s*"
        r"(?P<currency>[$€£])?\s*(?P<value>\d[\d,]*(?:\.\d+)?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"average price target(?:\s+for\s+[^.]{1,100})?\s+of\s*"
        r"(?P<currency>[$€£])?\s*(?P<value>\d[\d,]*(?:\.\d+)?)",
        re.IGNORECASE,
    ),
)
HIGH_PATTERN = re.compile(
    r"high forecast of\s*(?P<currency>[$€£])?\s*(?P<value>\d[\d,]*(?:\.\d+)?)",
    re.IGNORECASE,
)
LOW_PATTERN = re.compile(
    r"low forecast of\s*(?P<currency>[$€£])?\s*(?P<value>\d[\d,]*(?:\.\d+)?)",
    re.IGNORECASE,
)
CURRENCY_CODES = {"$": "USD", "€": "EUR", "£": "GBP"}


# Parsed analyst-consensus values from one rendered TipRanks forecast page.
@dataclass(frozen=True)
class TipRanksTarget:
    mean_price_target: float
    high_price_target: float | None
    low_price_target: float | None
    analyst_count: int | None
    currency: str
    source_url: str


# Raised when a page loads but does not contain a trustworthy forecast payload.
class TipRanksParseError(RuntimeError):
    pass


# Raised when the local browser service cannot be started or queried.
class TipRanksBrowserError(RuntimeError):
    pass


# Build the public forecast-page URL for a held ticker.
def tipranks_forecast_url(ticker: str) -> str:
    slug = quote(ticker.strip().lower().replace("/", "-"), safe=".-")
    return f"{TIPRANKS_BASE_URL}/{slug}/forecast"


# Parse the visible forecast prose and return None only for an explicit no-coverage page.
def parse_tipranks_forecast(
    ticker: str,
    page_text: str,
    *,
    source_url: str | None = None,
) -> TipRanksTarget | None:
    source_url = source_url or tipranks_forecast_url(ticker)
    text = " ".join((page_text or "").replace("\xa0", " ").split())
    lowered = text.lower()
    if any(pattern in lowered for pattern in NO_COVERAGE_PATTERNS):
        return None

    mean_match = _first_match(MEAN_PATTERNS, text)
    if mean_match is None:
        if _first_match(EMPTY_FORECAST_PATTERNS, text) is not None:
            return None
        if "access denied" in lowered or "verify you are human" in lowered:
            raise TipRanksParseError("TipRanks blocked the browser page")
        raise TipRanksParseError("average price target was not found")

    count = _first_int(COUNT_PATTERNS, text)
    high = _matched_price(HIGH_PATTERN.search(text))
    low = _matched_price(LOW_PATTERN.search(text))
    mean = _matched_price(mean_match)
    if mean is None or mean <= 0:
        raise TipRanksParseError("average price target was invalid")
    if high is not None and high < mean:
        raise TipRanksParseError("high price target was below the mean")
    if low is not None and low > mean:
        raise TipRanksParseError("low price target was above the mean")
    if count is not None and count <= 0:
        raise TipRanksParseError("analyst count was invalid")

    currency_symbol = mean_match.groupdict().get("currency")
    return TipRanksTarget(
        mean_price_target=mean,
        high_price_target=high,
        low_price_target=low,
        analyst_count=count,
        currency=CURRENCY_CODES.get(currency_symbol or "", "USD"),
        source_url=source_url,
    )


# Return the first regular-expression match from an ordered pattern list.
def _first_match(patterns: tuple[re.Pattern[str], ...], text: str) -> re.Match[str] | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match is not None:
            return match
    return None


# Extract a positive integer from the first matching analyst-count pattern.
def _first_int(patterns: tuple[re.Pattern[str], ...], text: str) -> int | None:
    match = _first_match(patterns, text)
    return int(match.group(1)) if match is not None else None


# Convert a matched localized price string to a float.
def _matched_price(match: re.Match[str] | None) -> float | None:
    if match is None:
        return None
    try:
        return float(match.group("value").replace(",", ""))
    except (TypeError, ValueError):
        return None


# Minimal client for the bundled Camoufox browser server.
class TipRanksBrowserClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BROWSER_URL,
        *,
        render_wait_seconds: float = 3.0,
        timeout_seconds: float = 40.0,
        max_attempts: int = 2,
        retry_wait_seconds: float = 1.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.render_wait_seconds = max(0.0, render_wait_seconds)
        self.max_attempts = max(1, max_attempts)
        self.retry_wait_seconds = max(0.0, retry_wait_seconds)
        self.client = httpx.Client(timeout=timeout_seconds)

    # Close the underlying HTTP connection pool.
    def close(self) -> None:
        self.client.close()

    # Fetch and parse one ticker's rendered TipRanks forecast page.
    def fetch_target(self, ticker: str) -> TipRanksTarget | None:
        last_error: TipRanksBrowserError | None = None
        for attempt in range(self.max_attempts):
            try:
                return self._fetch_target_once(ticker)
            except TipRanksBrowserError as exc:
                last_error = exc
                if attempt < self.max_attempts - 1 and self.retry_wait_seconds:
                    time.sleep(self.retry_wait_seconds)
        assert last_error is not None
        raise last_error

    # Run one isolated browser-tab attempt so transient server failures can be retried.
    def _fetch_target_once(self, ticker: str) -> TipRanksTarget | None:
        user_id = "sma-tipranks"
        session_key = f"target-{ticker.lower()}"
        url = tipranks_forecast_url(ticker)
        tab_id: str | None = None
        try:
            response = self.client.post(
                f"{self.base_url}/tabs",
                json={"userId": user_id, "sessionKey": session_key, "url": url},
            )
            response.raise_for_status()
            tab_id = response.json()["tabId"]
            time.sleep(self.render_wait_seconds)
            text = self._body_text(tab_id, user_id)
            if len(text.strip()) < 100:
                time.sleep(min(2.0, self.render_wait_seconds))
                text = self._body_text(tab_id, user_id)
            return parse_tipranks_forecast(ticker, text, source_url=url)
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise TipRanksBrowserError(f"browser request failed for {ticker}: {exc}") from exc
        finally:
            if tab_id is not None:
                try:
                    self.client.delete(
                        f"{self.base_url}/tabs/{tab_id}",
                        params={"userId": user_id},
                    )
                except httpx.HTTPError:
                    log.warning("tipranks_tab_close_failed", extra={"ticker": ticker})

    # Read rendered body text through the browser server's page-evaluate endpoint.
    def _body_text(self, tab_id: str, user_id: str) -> str:
        response = self.client.post(
            f"{self.base_url}/tabs/{tab_id}/evaluate",
            json={
                "userId": user_id,
                "expression": "document.body ? document.body.innerText : ''",
            },
        )
        response.raise_for_status()
        result = response.json().get("result")
        return result if isinstance(result, str) else ""


# Start the bundled local browser only when it is not already healthy.
@contextmanager
def ensure_tipranks_browser(
    *,
    base_url: str = DEFAULT_BROWSER_URL,
    command: str = DEFAULT_BROWSER_COMMAND,
    start_if_needed: bool = True,
    start_timeout_seconds: float = 35.0,
) -> Iterator[str]:
    if _browser_is_healthy(base_url):
        yield base_url
        return
    if not start_if_needed:
        raise TipRanksBrowserError(f"browser service is unavailable at {base_url}")

    args = shlex.split(command)
    if not args:
        raise TipRanksBrowserError("TIPRANKS_BROWSER_COMMAND is empty")
    process = subprocess.Popen(  # noqa: S603 - command is an operator-controlled setting.
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + max(1.0, start_timeout_seconds)
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise TipRanksBrowserError(
                    f"browser command exited with status {process.returncode}"
                )
            if _browser_is_healthy(base_url):
                yield base_url
                return
            time.sleep(0.5)
        raise TipRanksBrowserError(f"browser service did not become healthy at {base_url}")
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


# Check the browser service health endpoint without surfacing transient errors.
def _browser_is_healthy(base_url: str) -> bool:
    try:
        response = httpx.get(f"{base_url.rstrip('/')}/health", timeout=2.0)
        return response.status_code == 200
    except httpx.HTTPError:
        return False
