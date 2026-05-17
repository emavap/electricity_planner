"""Tiny tests for missed single-line branches."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.electricity_planner.const import DOMAIN
from custom_components.electricity_planner.defaults import (
    calculate_soc_price_multiplier,
)
from custom_components.electricity_planner.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.electricity_planner.feedin_decision import (
    FeedInDecisionCalculator,
)


def test_soc_price_multiplier_invalid_range_between_thresholds():
    assert (
        calculate_soc_price_multiplier(
            current_soc=15,
            emergency_soc=10,
            buffer_target_soc=10,
            max_multiplier=1.3,
        )
        == 1.0
    )


@pytest.mark.asyncio
async def test_diagnostics_reports_missing_coordinator():
    hass = SimpleNamespace(data={DOMAIN: {}})
    entry = MockConfigEntry(domain=DOMAIN)

    assert await async_get_config_entry_diagnostics(hass, entry) == {
        "error": "coordinator_unavailable"
    }


def test_feed_in_decision_uses_current_price_when_raw_price_missing():
    settings = SimpleNamespace(
        feedin_adjustment_multiplier=1,
        feedin_adjustment_offset=0,
        feedin_threshold=0.2,
    )
    ctx = SimpleNamespace(
        has_price_data=True,
        current_price=0.25,
        raw_current_price=None,
        remaining_solar=1234,
    )

    decision = FeedInDecisionCalculator(settings).decide(ctx)

    assert decision["feedin_solar"] is True
    assert decision["feedin_effective_price"] == 0.25


def test_feed_in_decision_falls_back_to_raw_price_when_adjustment_invalid():
    settings = SimpleNamespace(
        feedin_adjustment_multiplier="bad",
        feedin_adjustment_offset=0,
        feedin_threshold=0.2,
    )
    ctx = SimpleNamespace(
        has_price_data=True,
        current_price=0.25,
        raw_current_price=0.25,
        remaining_solar=1234,
    )

    decision = FeedInDecisionCalculator(settings).decide(ctx)

    assert decision["feedin_solar"] is True
    assert decision["feedin_effective_price"] == 0.25
