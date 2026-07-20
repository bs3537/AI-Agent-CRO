"""Static contract checks for the Healthcare Movers frontend."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "frontend" / "src"


def test_healthcare_movers_page_has_required_rank_fields_and_controls():
    source = (ROOT / "components" / "HealthcareMoversPage.tsx").read_text()

    for required in (
        "Healthcare Movers",
        "company_name",
        "row.ticker",
        "return_pct",
        "Market cap",
        "Volume",
        "Gainers",
        "Decliners",
        "WINDOWS.map",
        "tabRefreshStamp",
        "America/New_York",
    ):
        assert required in source


def test_dashboard_links_to_hash_routed_movers_page():
    source = (ROOT / "App.tsx").read_text()
    api = (ROOT / "api.ts").read_text()

    assert "#/healthcare-movers" in source
    assert "Open healthcare movers" in source
    assert "/api/healthcare-movers" in api
