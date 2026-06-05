from __future__ import annotations

from sma_monitor.db import connection
from sma_monitor.paths import DATA_ROOT
from sma_monitor.portfolio.sidecar import (
    init_sidecar_schema,
    load_all_sidecars,
    load_sidecar,
    set_thesis,
    sidecar_path,
)
from sma_monitor.portfolio.uploads import combined_text, list_files, save_upload


def test_sidecar_seed_uses_repo_yaml_when_runtime_data_root_is_empty(monkeypatch, tmp_path):
    import sma_monitor.portfolio.sidecar as sidecar_mod

    empty_runtime_sidecars = tmp_path / "portfolio" / "sidecar"
    monkeypatch.setattr(sidecar_mod, "SIDECAR_DIR", empty_runtime_sidecars)
    init_sidecar_schema(seed_from_yaml=False)
    with connection() as conn:
        before = [dict(r) for r in conn.execute("SELECT * FROM portfolio_sidecars")]

    try:
        with connection() as conn:
            conn.execute("DELETE FROM portfolio_sidecars")

        init_sidecar_schema()

        assert load_sidecar("AQST") is not None
        assert load_sidecar("ABVX") is not None
        assert not empty_runtime_sidecars.exists()
    finally:
        with connection() as conn:
            conn.execute("DELETE FROM portfolio_sidecars")
            for row in before:
                conn.execute(
                    """INSERT INTO portfolio_sidecars
                       (ticker, payload_json, source, updated_at)
                       VALUES (?, ?, ?, ?)""",
                    (
                        row["ticker"],
                        row["payload_json"],
                        row["source"],
                        row["updated_at"],
                    ),
                )


def test_sidecar_db_overrides_yaml_seed_without_rewriting_yaml():
    init_sidecar_schema()
    original_yaml = sidecar_path("VRTX").read_text()
    new_thesis = "DB-backed thesis edit survives Replit filesystem resets."

    set_thesis("VRTX", new_thesis)

    assert load_sidecar("VRTX").thesis == new_thesis
    assert load_all_sidecars()["VRTX"].thesis == new_thesis
    assert sidecar_path("VRTX").read_text() == original_yaml


def test_uploaded_text_survives_missing_cached_text_file():
    rec = save_upload("VRTX", "durable.md", b"# Durable\n\nThis text is stored in the DB.")
    text_path = DATA_ROOT / rec["text_path"]
    assert text_path.exists()
    text_path.unlink()

    assert "stored in the DB" in combined_text("VRTX")
    row = next(r for r in list_files("VRTX") if r["event_id"] == rec["event_id"])
    assert "stored in the DB" in row["text_content"]
