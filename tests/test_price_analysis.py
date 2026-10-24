"""Regression tests for consistent all-in fallback buy prices."""

import pytest

from custom_components.electricity_planner.decision_engine import ChargingDecisionEngine


@pytest.mark.parametrize("raw", [0.20, 0.0, -0.10])
def test_fallback_prices_include_vat_once(raw):
    engine = ChargingDecisionEngine(
        hass=None,
        config={
            "buy_vat_multiplier": 1.21,
            "price_threshold": 0.28,
            "price_adjustment_multiplier": 1.0,
            "price_adjustment_offset": 0.0,
        },
    )
    data = {
        "current_price": raw,
        "highest_price": raw,
        "lowest_price": raw,
        "next_price": raw,
        "transport_cost": 0.05,
    }
    fallback = engine._price_analysis.analyze(data)
    expected = (raw + 0.05) * 1.21
    for key in ("current_price", "highest_price", "lowest_price", "next_price"):
        assert fallback[key] == pytest.approx(expected)
    assert fallback["is_low_price"] == (expected <= 0.28)
    overrides = {
        key: expected
        for key in ("current_price", "highest_price", "lowest_price", "next_price")
    }
    interval = engine._price_analysis.analyze(
        {**data, "price_analysis_overrides": overrides}
    )
    assert interval["current_price"] == pytest.approx(expected)
