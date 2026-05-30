"""OpenRouter fallback cost accounting."""
from __future__ import annotations

import pytest

from sma_monitor.db import connection
from sma_monitor.llm.openrouter_client import OpenRouterProvider
from sma_monitor.orchestrator.cost import (
    estimate_openrouter_cost_usd,
    record_llm_call,
    record_openrouter_usage,
)


def test_estimate_openrouter_cost_uses_configured_model_prices():
    assert estimate_openrouter_cost_usd(
        "openrouter:xiaomi/mimo-v2.5-pro",
        prompt_tokens=1_000,
        completion_tokens=500,
    ) == pytest.approx(0.00087)


def test_record_openrouter_usage_adds_paid_cost_row():
    cost = record_openrouter_usage(
        kind="decision",
        model="openrouter:minimax/minimax-m2.7",
        usage={"prompt_tokens": 1_000, "completion_tokens": 500},
    )
    assert cost == pytest.approx(0.00086)

    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM cost_ledger WHERE model = ? AND kind = ? "
            "ORDER BY incurred_at DESC LIMIT 1",
            ("openrouter:minimax/minimax-m2.7", "decision"),
        ).fetchone()
    assert row is not None
    assert row["input_tokens"] == 1_000
    assert row["output_tokens"] == 500
    assert row["cost_usd"] == pytest.approx(0.00086)


def test_generic_zero_cost_logger_skips_openrouter_duplicates():
    with connection() as conn:
        before = conn.execute(
            "SELECT COUNT(*) AS n FROM cost_ledger WHERE model = ?",
            ("openrouter:skip-duplicate-test",),
        ).fetchone()["n"]
    record_llm_call(kind="decision", model="openrouter:skip-duplicate-test")
    with connection() as conn:
        after = conn.execute(
            "SELECT COUNT(*) AS n FROM cost_ledger WHERE model = ?",
            ("openrouter:skip-duplicate-test",),
        ).fetchone()["n"]
    assert after == before


def test_openrouter_provider_records_response_usage(monkeypatch):
    import sma_monitor.llm.openrouter_client as openrouter

    class _Response:
        status_code = 200
        text = "{}"

        def json(self):
            return {
                "id": "chatcmpl-test",
                "model": "xiaomi/mimo-v2.5-pro",
                "choices": [{"message": {"content": "ok"}}],
                "usage": {"prompt_tokens": 2_000, "completion_tokens": 1_000},
            }

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            return _Response()

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(openrouter.httpx, "Client", _Client)

    provider = OpenRouterProvider(model="xiaomi/mimo-v2.5-pro", cost_kind="chat")
    assert provider.complete_text(system="s", user="u") == "ok"

    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM cost_ledger WHERE model = ? AND kind = ? "
            "ORDER BY incurred_at DESC LIMIT 1",
            ("openrouter:xiaomi/mimo-v2.5-pro", "chat"),
        ).fetchone()
    assert row is not None
    assert row["input_tokens"] == 2_000
    assert row["output_tokens"] == 1_000
    assert row["cost_usd"] == pytest.approx(0.00174)
