"""Diagnostics describe observed evaluation time and distinct price ceilings."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.electricity_planner.const import DOMAIN
from custom_components.electricity_planner.decision_engine import ChargingDecisionEngine
from custom_components.electricity_planner.sensor import (
    DecisionDiagnosticsSensor,
    PriceThresholdSensor,
)


def _coordinator(data):
    return SimpleNamespace(
        data=data,
        config={"price_threshold": 0.25},
        async_add_listener=lambda *args, **kwargs: lambda: None,
        last_update_success=True,
    )


def test_engine_records_evaluation_time_separately_from_next(monkeypatch):
    now = datetime(2026, 10, 26, 17, 9, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "custom_components.electricity_planner.decision_engine.dt_util.utcnow",
        lambda: now,
    )
    data = ChargingDecisionEngine(hass=None, config={})._initialize_decision_data()
    assert data["evaluated_at"] == now
    assert data["next_evaluation"] > now


def test_diagnostics_never_reports_next_as_last():
    now = datetime(2026, 10, 26, 17, 9, tzinfo=timezone.utc)
    coordinator = _coordinator(
        {"evaluated_at": now, "next_evaluation": now + timedelta(minutes=5)}
    )
    sensor = DecisionDiagnosticsSensor(
        coordinator, MockConfigEntry(domain=DOMAIN, data={})
    )
    assert sensor.extra_state_attributes["last_evaluation"] == now
    assert sensor.extra_state_attributes["next_evaluation"] == now + timedelta(
        minutes=5
    )
    del coordinator.data["evaluated_at"]
    assert sensor.extra_state_attributes["last_evaluation"] is None


@pytest.mark.parametrize("price,below", [(0.25, True), (0.26, False)])
def test_configured_threshold_is_distinct_and_inclusive(price, below):
    coordinator = _coordinator(
        {
            "price_analysis": {"current_price": price, "price_threshold": 0.3147},
            "battery_stable_threshold": 0.3147,
            "battery_effective_threshold": 0.3403,
        }
    )
    sensor = PriceThresholdSensor(coordinator, MockConfigEntry(domain=DOMAIN, data={}))
    attrs = sensor.extra_state_attributes
    assert sensor.native_value == 0.25
    assert attrs["configured_threshold"] == 0.25
    assert attrs["resolved_price_threshold"] == 0.3147
    assert attrs["battery_stable_threshold"] == 0.3147
    assert attrs["battery_effective_threshold"] == 0.3403
    assert attrs["price_below_threshold"] is below
