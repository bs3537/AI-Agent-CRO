"""Healthcare-mover ranking and persistence tests."""
from __future__ import annotations

import pytest

from sma_monitor.db import connection
from sma_monitor.healthcare_movers.ranking import compute_mover_rankings
from sma_monitor.healthcare_movers.store import (
    active_universe,
    price_histories,
    save_price_points,
    save_ranking_run,
    save_universe_snapshot,
)


def _universe():
    return [
        {
            "ticker": "JSPR",
            "company_name": "Jasper Therapeutics, Inc.",
            "sector": "Healthcare",
            "industry": "Biotechnology",
            "exchange": "NASDAQ",
            "country": "US",
            "market_cap": 14_383_524,
        },
        {
            "ticker": "FALL",
            "company_name": "Falling Bio",
            "sector": "Healthcare",
            "industry": "Biotechnology",
            "exchange": "NASDAQ",
            "country": "US",
            "market_cap": 20_000_000,
        },
    ]


def _histories():
    dates = [
        "2026-07-10",
        "2026-07-13",
        "2026-07-14",
        "2026-07-15",
        "2026-07-16",
        "2026-07-17",
    ]
    jspr = [0.5795, 0.6079, 0.7228, 0.692, 0.774, 0.885]
    fall = [10.0, 9.8, 9.4, 9.0, 8.7, 8.0]
    return {
        "JSPR": [
            {"price_date": date, "close": close, "volume": 1_000_000 + index * 100_000}
            for index, (date, close) in enumerate(zip(dates, jspr, strict=True))
        ],
        "FALL": [
            {"price_date": date, "close": close, "volume": 500_000}
            for date, close in zip(dates, fall, strict=True)
        ],
    }


def test_jspr_five_session_move_and_company_fields():
    result = compute_mover_rankings(_universe(), _histories())

    row = result["rankings"]["5"]["gainers"][0]
    assert result["as_of_date"] == "2026-07-17"
    assert row["rank"] == 1
    assert row["ticker"] == "JSPR"
    assert row["company_name"] == "Jasper Therapeutics, Inc."
    assert row["industry"] == "Biotechnology"
    assert row["price"] == pytest.approx(0.885)
    assert row["return_pct"] == pytest.approx((0.885 / 0.5795 - 1) * 100)
    assert row["start_date"] == "2026-07-10"
    assert row["end_date"] == "2026-07-17"
    assert "under_one_dollar" in row["flags"]


def test_all_requested_windows_and_decliners_are_ranked():
    result = compute_mover_rankings(_universe(), _histories())

    assert set(result["rankings"]) == {"1", "2", "3", "4", "5"}
    assert result["rankings"]["1"]["gainers"][0]["ticker"] == "JSPR"
    assert result["rankings"]["5"]["decliners"][0]["ticker"] == "FALL"
    assert result["covered_by_window"]["5"] == 2


def test_missing_start_session_is_not_ranked_for_that_window():
    histories = _histories()
    histories["JSPR"] = histories["JSPR"][1:]

    result = compute_mover_rankings(_universe(), histories)

    five_day_tickers = {
        row["ticker"]
        for direction in result["rankings"]["5"].values()
        for row in direction
    }
    assert "JSPR" not in five_day_tickers
    assert result["covered_by_window"]["5"] == 1


def test_sub_penny_move_is_covered_but_not_promoted_without_dollar_volume():
    universe = [
        *_universe(),
        {
            "ticker": "MICRO",
            "company_name": "Microcap Bio",
            "sector": "Healthcare",
            "industry": "Biotechnology",
            "exchange": "OTC",
            "country": "US",
            "market_cap": 500_000,
        },
    ]
    histories = _histories()
    histories["MICRO"] = [
        {
            "price_date": row["price_date"],
            "close": 0.0001 if index == 5 else 0.000001,
            "volume": 1_000_000,
        }
        for index, row in enumerate(histories["JSPR"])
    ]

    result = compute_mover_rankings(universe, histories)

    ranked = {
        row["ticker"]
        for direction in result["rankings"]["5"].values()
        for row in direction
    }
    assert result["covered_by_window"]["5"] == 3
    assert "MICRO" not in ranked
    assert result["rankings"]["5"]["gainers"][0]["ticker"] == "JSPR"


def test_store_round_trip_preserves_rankings():
    universe = _universe()
    histories = _histories()
    save_universe_snapshot(universe)
    points = [
        {"ticker": ticker, **point}
        for ticker, rows in histories.items()
        for point in rows
    ]
    assert save_price_points(points) == 12
    stored_universe = [
        row for row in active_universe() if row["ticker"] in {"JSPR", "FALL"}
    ]
    stored_histories = price_histories(["JSPR", "FALL"])
    result = compute_mover_rankings(stored_universe, stored_histories)
    run_id = save_ranking_run(result)

    with connection() as conn:
        run = conn.execute(
            "SELECT as_of_date FROM healthcare_mover_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        leader = conn.execute(
            """SELECT ticker, company_name FROM healthcare_mover_rankings
               WHERE run_id = ? AND window_days = 5 AND direction = 'gainers'
               ORDER BY rank LIMIT 1""",
            (run_id,),
        ).fetchone()
    assert run["as_of_date"] == "2026-07-17"
    assert leader["ticker"] == "JSPR"
    assert leader["company_name"].startswith("Jasper")


def test_price_store_chunks_large_upserts_and_updates_conflicts():
    points = [
        {
            "ticker": "BULK",
            "price_date": f"2026-{index:03d}",
            "close": float(index),
            "volume": index * 100,
        }
        for index in range(1, 206)
    ]

    assert save_price_points(points) == 205
    assert save_price_points(
        [{"ticker": "BULK", "price_date": "2026-205", "close": 999, "volume": 1}]
    ) == 1
    history = price_histories(["BULK"], max_sessions=300)["BULK"]
    assert len(history) == 205
    assert history[-1]["close"] == 999
