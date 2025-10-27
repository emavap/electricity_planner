"""Tests for manual override handling in the coordinator."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from custom_components.electricity_planner import coordinator as coordinator_module
from custom_components.electricity_planner.coordinator import ElectricityPlannerCoordinator
from custom_components.electricity_planner.const import (
    CONF_BATTERY_SOC_ENTITIES,
    CONF_CURRENT_PRICE_ENTITY,
    CONF_GRID_POWER_ENTITY,
    CONF_HIGHEST_PRICE_ENTITY,
    CONF_LOWEST_PRICE_ENTITY,
    CONF_NEXT_PRICE_ENTITY,
    DOMAIN,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry


class FakeState:
    def __init__(self, state: str):
        self.state = state


class FakeStates:
    def __init__(self):
        self._states: dict[str, FakeState] = {}

    def set(self, entity_id: str, value: str) -> None:
        self._states[entity_id] = FakeState(str(value))

    def get(self, entity_id: str) -> FakeState | None:
        return self._states.get(entity_id)


class FakeHass:
    def __init__(self):
        self.loop = None
        self.states = FakeStates()
        self.data: dict = {}
        self.config_entries = SimpleNamespace(_entries={})


@pytest.fixture
def fake_hass():
    return FakeHass()


def _base_config() -> dict[str, object]:
    return {
        CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
        CONF_HIGHEST_PRICE_ENTITY: "sensor.highest_price",
        CONF_LOWEST_PRICE_ENTITY: "sensor.lowest_price",
        CONF_NEXT_PRICE_ENTITY: "sensor.next_price",
        CONF_GRID_POWER_ENTITY: "sensor.grid_power",
        CONF_BATTERY_SOC_ENTITIES: [],
    }


def _create_coordinator(fake_hass: FakeHass, config: dict[str, object], monkeypatch: pytest.MonkeyPatch) -> ElectricityPlannerCoordinator:
    """Create a coordinator instance without setting up listeners."""
    entry = MockConfigEntry(domain=DOMAIN, data=config, options={})
    monkeypatch.setattr(
        coordinator_module.ElectricityPlannerCoordinator,
        "_setup_entity_listeners",
        lambda self: None,
    )
    return ElectricityPlannerCoordinator(fake_hass, entry)


def test_apply_manual_override_updates_decision(fake_hass, monkeypatch):
    """Ensure active overrides update decisions and trace data."""
    base_time = datetime(2025, 10, 27, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(coordinator_module.dt_util, "utcnow", lambda: base_time, raising=False)

    config = _base_config()
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    coordinator._manual_overrides["car_grid_charging"] = {
        "value": True,
        "reason": None,
        "expires_at": base_time + timedelta(minutes=10),
        "set_at": base_time,
    }

    decision = {
        "car_grid_charging": False,
        "car_grid_charging_reason": None,
        "battery_grid_charging": False,
        "strategy_trace": [],
    }

    updated_decision, changed_targets = coordinator._apply_manual_overrides(decision)

    assert updated_decision["car_grid_charging"] is True
    assert updated_decision["car_grid_charging_reason"] == "Manual override: Manual override to charge"
    assert changed_targets == {"car_grid_charging"}

    overrides = updated_decision.get("manual_overrides", {})
    assert "car_grid_charging" in overrides
    assert overrides["car_grid_charging"]["reason"] == "Manual override to charge"
    assert overrides["car_grid_charging"]["value"] is True
    assert overrides["car_grid_charging"]["set_at"] == base_time.isoformat()

    trace = updated_decision.get("strategy_trace", [])
    assert trace and trace[-1]["strategy"] == "ManualOverride"
    assert trace[-1]["target"] == "car_grid_charging"


def test_expired_manual_override_is_cleared(fake_hass, monkeypatch):
    """Expired overrides should be removed and not affect decisions."""
    base_time = datetime(2025, 10, 27, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(coordinator_module.dt_util, "utcnow", lambda: base_time, raising=False)

    config = _base_config()
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    coordinator._manual_overrides["car_grid_charging"] = {
        "value": False,
        "reason": "force wait",
        "expires_at": base_time - timedelta(minutes=1),
        "set_at": base_time - timedelta(minutes=5),
    }

    decision = {
        "car_grid_charging": True,
        "car_grid_charging_reason": "Existing reason",
        "strategy_trace": [{"strategy": "Base", "target": "car_grid_charging"}],
    }

    updated_decision, changed_targets = coordinator._apply_manual_overrides(decision)

    assert updated_decision["car_grid_charging"] is True
    assert updated_decision["car_grid_charging_reason"] == "Existing reason"
    assert changed_targets == set()
    assert coordinator._manual_overrides["car_grid_charging"] is None

    # Manual overrides block should still be present but empty
    assert updated_decision.get("manual_overrides") == {}
    assert updated_decision["strategy_trace"] == decision["strategy_trace"]
