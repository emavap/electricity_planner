"""Relaxation must never tighten negative price ceilings."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from custom_components.electricity_planner.charging_window import (
    ChargingWindowValidator,
)
from custom_components.electricity_planner.decision_engine import (
    CycleContext,
    EngineSettings,
)
from custom_components.electricity_planner.helpers import DataValidator
from custom_components.electricity_planner.strategies import StrategyManager


@pytest.mark.parametrize("base,expected", [(-0.1, -0.08), (0.0, 0.0), (0.1, 0.12)])
def test_cycle_threshold_relaxation_signs(base, expected):
    settings = EngineSettings.from_config(
        {
            "price_threshold": base,
            "car_permissive_threshold_multiplier": 1.2,
            "soc_price_multiplier_max": 1.2,
        },
        DataValidator(),
    )
    ctx = CycleContext.from_data(
        {"car_permissive_mode_active": True},
        settings,
        {"average_soc": 15},
        {"price_threshold": base},
        {},
        {},
    )
    assert ctx.effective_battery_price_threshold == pytest.approx(expected)
    assert ctx.effective_car_price_threshold == pytest.approx(expected)


@pytest.mark.parametrize(
    "price,allowed", [(-0.13, True), (-0.1, True), (-0.09, True), (-0.07, False)]
)
def test_negative_soc_relaxation_guard(price, allowed):
    context = {
        "price_analysis": {"current_price": price, "price_threshold": -0.1},
        "battery_analysis": {"average_soc": 20, "max_soc_threshold": 90},
        "power_analysis": {"solar_surplus": 0, "significant_solar_surplus": False},
        "config": {
            "emergency_soc_threshold": 15,
            "soc_buffer_target": 50,
            "soc_price_multiplier_max": 1.3,
        },
    }
    result, _ = StrategyManager(use_dynamic_threshold=True).evaluate(context)
    assert result is allowed


@pytest.mark.parametrize(
    "price,allowed", [(-0.13, True), (-0.1, True), (-0.09, True), (-0.07, False)]
)
def test_negative_permissive_window(price, allowed, monkeypatch):
    now = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "custom_components.electricity_planner.charging_window.dt_util.utcnow",
        lambda: now,
    )
    coordinator = SimpleNamespace(
        config={"price_threshold": -0.1, "min_car_charging_duration": 2},
        _compute_price_data_hash=lambda *args: "test",
        _should_use_average_threshold=lambda _: False,
        _build_price_timeline=lambda *args: [(now, now + timedelta(hours=2), price)],
    )
    assert (
        ChargingWindowValidator(coordinator).check(
            {"BE": []},
            None,
            None,
            None,
            permissive_mode_active=True,
            permissive_multiplier=1.2,
        )
        is allowed
    )


@pytest.mark.parametrize("base", [-0.1, 0.0, 0.1])
@pytest.mark.parametrize("multiplier", [0.5, 1.0, 1.5, 2.0])
def test_relaxation_never_tightens(base, multiplier):
    from custom_components.electricity_planner.defaults import relax_price_threshold

    assert relax_price_threshold(base, multiplier) >= base
    if multiplier <= 1:
        assert relax_price_threshold(base, multiplier) == base


@pytest.mark.parametrize("soc,expected", [(15, True), (15.001, False)])
def test_negative_threshold_emergency_boundary(soc, expected):
    context = {
        "price_analysis": {"current_price": 0.5, "price_threshold": -0.1},
        "battery_analysis": {"average_soc": soc, "max_soc_threshold": 90},
        "config": {"emergency_soc_threshold": 15},
    }
    result, _ = StrategyManager(use_dynamic_threshold=True).evaluate(context)
    assert result is expected
