from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sma_monitor.portfolio.flex import parse_positions


def test_parse_positions_uses_cost_basis_price_when_money_missing():
    xml = """\
<FlexQueryResponse>
  <FlexStatements count="1">
    <FlexStatement>
      <EquitySummaryInBase total="10000" />
      <OpenPositions>
        <OpenPosition symbol="AQST" position="100" positionValue="450" costBasisPrice="3.25" />
      </OpenPositions>
    </FlexStatement>
  </FlexStatements>
</FlexQueryResponse>
"""
    positions, nav = parse_positions(xml, pulled_at=datetime(2026, 5, 30, tzinfo=UTC))
    assert nav == 10000
    assert positions[0].cost_basis == pytest.approx(325.0)
