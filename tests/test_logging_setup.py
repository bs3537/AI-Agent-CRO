"""Structured logging safety tests."""
from __future__ import annotations

import logging

from sma_monitor.logging_setup import setup_logging


# HTTP request loggers stay above INFO so query-string credentials are not emitted.
def test_setup_logging_suppresses_http_client_request_urls():
    setup_logging("INFO")

    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING
