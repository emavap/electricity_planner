"""Unit tests for forecast summary calculator edge cases."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.util import dt as dt_util

from custom_components.electricity_planner.const import CONF_MIN_CAR_CHARGING_DURATION
from custom_components.electricity_planner.forecast_summary import (
    ForecastSummaryCalculator,
)


class _Coordinator:
    def __init__(self, timeline=None):
        self.config = {CONF_MIN_CAR_CHARGING_DURATION: 2}
        self._last_price_timeline = timeline
        self._last_price_timeline_generated_at = None
        self._last_price_timeline_data_hash = None
        self._price_timeline_max_age = timedelta(hours=1)
        self.build_calls = 0

    def _compute_price_data_hash(self, prices_today, prices_tomorrow):
        return (repr(prices_today), repr(prices_tomorrow))

    def _build_price_timeline(
        self,
        prices_today,
        prices_tomorrow,
        transport_lookup,
        current_transport_cost,
        now,
    ):
        self.build_calls += 1
        return list(self._built_timeline)


def test_forecast_summary_builds_and_caches_best_window():
    now = dt_util.utcnow()
    timeline = [
        (now - timedelta(hours=1), now + timedelta(hours=1), 0.30),
        (now + timedelta(hours=1), now + timedelta(hours=2), 0.10),
        (now + timedelta(hours=2), now + timedelta(hours=3), 0.20),
    ]
    coordinator = _Coordinator()
    coordinator._built_timeline = timeline

    summary = ForecastSummaryCalculator(coordinator).calculate(
        prices_today={"raw_today": [1]},
        prices_tomorrow=None,
        transport_lookup=None,
        current_transport_cost=None,
        minimum_average_threshold=0.25,
    )

    assert summary["available"] is True
    assert summary["cheapest_interval_price"] == 0.1
    assert summary["best_window_hours"] == 2
    assert summary["best_window_average_price"] == 0.15
    assert summary["average_threshold"] == 0.25
    assert "timeline_generated_at" in summary
    assert coordinator.build_calls == 1
    assert coordinator._last_price_timeline == timeline


def test_forecast_summary_uses_stale_cache_when_source_data_missing():
    now = dt_util.utcnow()
    timeline = [(now, now + timedelta(hours=1), 0.12)]
    coordinator = _Coordinator(timeline=timeline)
    coordinator._last_price_timeline_generated_at = now
    coordinator.config = {CONF_MIN_CAR_CHARGING_DURATION: 1}

    summary = ForecastSummaryCalculator(coordinator).calculate(
        prices_today=None,
        prices_tomorrow=None,
        transport_lookup=None,
        current_transport_cost=None,
        minimum_average_threshold=None,
    )

    assert summary["available"] is True
    assert summary["stale"] is True
    assert summary["cheapest_interval_price"] == 0.12


def test_forecast_summary_expires_cache_and_reports_unavailable():
    now = dt_util.utcnow()
    old_timeline = [(now, now + timedelta(hours=1), 0.12)]
    coordinator = _Coordinator(timeline=old_timeline)
    coordinator._last_price_timeline_generated_at = now - timedelta(hours=2)
    coordinator._last_price_timeline_data_hash = "old"

    summary = ForecastSummaryCalculator(coordinator).calculate(
        prices_today=None,
        prices_tomorrow=None,
        transport_lookup=None,
        current_transport_cost=None,
        minimum_average_threshold=None,
    )

    assert summary == {"available": False}
    assert coordinator._last_price_timeline is None
    assert coordinator._last_price_timeline_generated_at is None
    assert coordinator._last_price_timeline_data_hash is None


def test_forecast_summary_invalidates_cache_when_price_hash_changes():
    now = dt_util.utcnow()
    cached = [(now, now + timedelta(hours=1), 0.50)]
    rebuilt = [(now, now + timedelta(hours=1), 0.05)]
    coordinator = _Coordinator(timeline=cached)
    coordinator._built_timeline = rebuilt
    coordinator._last_price_timeline_generated_at = now
    coordinator._last_price_timeline_data_hash = "old-hash"
    coordinator.config = {CONF_MIN_CAR_CHARGING_DURATION: 1}

    summary = ForecastSummaryCalculator(coordinator).calculate(
        prices_today={"raw_today": [2]},
        prices_tomorrow=None,
        transport_lookup=None,
        current_transport_cost=None,
        minimum_average_threshold=None,
    )

    assert summary["available"] is True
    assert summary["cheapest_interval_price"] == 0.05
    assert coordinator.build_calls == 1
    assert coordinator._last_price_timeline == rebuilt


def test_find_best_window_returns_none_for_gap_shorter_than_duration():
    now = dt_util.utcnow()
    future_segments = [(now, now + timedelta(minutes=30), 0.1)]

    assert (
        ForecastSummaryCalculator._find_best_window(
            future_segments, now, timedelta(hours=1)
        )
        is None
    )


def test_forecast_cache_invalidated_by_tariff_or_config_change():
    now = dt_util.utcnow()
    coordinator = _Coordinator()
    calculator = ForecastSummaryCalculator(coordinator)
    coordinator._built_timeline = [(now, now + timedelta(hours=3), 0.10)]
    args = dict(
        prices_today={"raw_today": [1]},
        prices_tomorrow=None,
        transport_lookup=None,
        current_transport_cost=0.05,
        minimum_average_threshold=0.25,
    )
    assert calculator.calculate(**args)["cheapest_interval_price"] == 0.10
    calculator.calculate(**args)
    assert coordinator.build_calls == 1
    coordinator._built_timeline = [(now, now + timedelta(hours=3), 0.20)]
    args["current_transport_cost"] = 0.15
    assert calculator.calculate(**args)["cheapest_interval_price"] == 0.20
    assert coordinator.build_calls == 2
    coordinator.config["buy_vat_multiplier"] = 1.21
    calculator.calculate(**args)
    assert coordinator.build_calls == 3


def test_charging_window_and_summary_share_one_timeline_build():
    from custom_components.electricity_planner.charging_window import (
        ChargingWindowValidator,
    )

    now = dt_util.utcnow()
    coordinator = _Coordinator()
    coordinator._built_timeline = [
        (now - timedelta(minutes=1), now + timedelta(hours=3), 0.10)
    ]
    coordinator._should_use_average_threshold = lambda threshold: threshold is not None
    today = {"raw_today": [1]}
    ChargingWindowValidator(coordinator).check(
        today, None, None, 0.05, average_threshold=0.25
    )
    ForecastSummaryCalculator(coordinator).calculate(today, None, None, 0.05, 0.25)
    assert coordinator.build_calls == 1
