"""Tests for manual override handling in the coordinator."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.electricity_planner import coordinator as coordinator_module
from custom_components.electricity_planner.const import (
    CONF_BATTERY_SOC_ENTITIES,
    CONF_CURRENT_PRICE_ENTITY,
    CONF_GRID_POWER_ENTITY,
    CONF_HIGHEST_PRICE_ENTITY,
    CONF_LOWEST_PRICE_ENTITY,
    CONF_NEXT_PRICE_ENTITY,
    DOMAIN,
)
from custom_components.electricity_planner.coordinator import (
    ElectricityPlannerCoordinator,
)


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


def _create_coordinator(
    fake_hass: FakeHass, config: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> ElectricityPlannerCoordinator:
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
    monkeypatch.setattr(
        coordinator_module.dt_util, "utcnow", lambda: base_time, raising=False
    )

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
    assert (
        updated_decision["car_grid_charging_reason"]
        == "Manual override: Manual override to charge"
    )
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
    monkeypatch.setattr(
        coordinator_module.dt_util, "utcnow", lambda: base_time, raising=False
    )

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
    # After expiration, the key should be set to None (cleared)
    assert coordinator._manual_overrides["car_grid_charging"] is None

    # Manual overrides block should still be present but empty
    assert updated_decision.get("manual_overrides") == {}
    assert updated_decision["strategy_trace"] == decision["strategy_trace"]


def test_get_manual_override_clears_expired_override(fake_hass, monkeypatch):
    """Reading an expired override should treat it as inactive immediately."""
    base_time = datetime(2025, 10, 27, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        coordinator_module.dt_util, "utcnow", lambda: base_time, raising=False
    )

    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    coordinator._manual_overrides["battery_grid_charging"] = {
        "value": False,
        "reason": "temporary disable",
        "expires_at": base_time - timedelta(minutes=1),
        "set_at": base_time - timedelta(minutes=5),
    }

    override = coordinator.get_manual_override("battery_grid_charging")

    assert override is None
    assert coordinator._manual_overrides["battery_grid_charging"] is None


def test_apply_numeric_override_charger_limit(fake_hass, monkeypatch):
    """Ensure charger_limit numeric override updates decisions."""
    base_time = datetime(2025, 10, 27, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        coordinator_module.dt_util, "utcnow", lambda: base_time, raising=False
    )

    config = _base_config()
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    coordinator._manual_overrides["charger_limit"] = {
        "value": 5000,
        "reason": "Manual charger limit",
        "expires_at": base_time + timedelta(minutes=30),
        "set_at": base_time,
    }

    decision = {
        "charger_limit": 11000,
        "charger_limit_reason": "Automatic calculation",
        "strategy_trace": [],
    }

    updated_decision, changed_targets = coordinator._apply_manual_overrides(decision)

    assert updated_decision["charger_limit"] == 5000
    assert "override: Manual charger limit" in updated_decision["charger_limit_reason"]
    assert changed_targets == {"charger_limit"}

    overrides = updated_decision.get("manual_overrides", {})
    assert "charger_limit" in overrides
    assert overrides["charger_limit"]["value"] == 5000
    assert overrides["charger_limit"]["reason"] == "Manual charger limit"


def test_apply_numeric_override_grid_setpoint(fake_hass, monkeypatch):
    """Ensure grid_setpoint numeric override updates decisions."""
    base_time = datetime(2025, 10, 27, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        coordinator_module.dt_util, "utcnow", lambda: base_time, raising=False
    )

    config = _base_config()
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    coordinator._manual_overrides["grid_setpoint"] = {
        "value": 8000,
        "reason": "Manual grid setpoint",
        "expires_at": base_time + timedelta(hours=1),
        "set_at": base_time,
    }

    decision = {
        "grid_setpoint": 6000,
        "grid_setpoint_reason": "Automatic calculation",
        "strategy_trace": [],
    }

    updated_decision, changed_targets = coordinator._apply_manual_overrides(decision)

    assert updated_decision["grid_setpoint"] == 8000
    assert "override: Manual grid setpoint" in updated_decision["grid_setpoint_reason"]
    assert changed_targets == {"grid_setpoint"}

    overrides = updated_decision.get("manual_overrides", {})
    assert "grid_setpoint" in overrides
    assert overrides["grid_setpoint"]["value"] == 8000
    assert overrides["grid_setpoint"]["reason"] == "Manual grid setpoint"


def test_numeric_override_expires_correctly(fake_hass, monkeypatch):
    """Ensure expired numeric overrides are cleared."""
    base_time = datetime(2025, 10, 27, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        coordinator_module.dt_util, "utcnow", lambda: base_time, raising=False
    )

    config = _base_config()
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    coordinator._manual_overrides["charger_limit"] = {
        "value": 3000,
        "reason": "Temporary limit",
        "expires_at": base_time - timedelta(minutes=1),
        "set_at": base_time - timedelta(minutes=10),
    }

    decision = {
        "charger_limit": 11000,
        "charger_limit_reason": "Automatic",
        "strategy_trace": [],
    }

    updated_decision, changed_targets = coordinator._apply_manual_overrides(decision)

    # Expired override should not affect decision
    assert updated_decision["charger_limit"] == 11000
    assert updated_decision["charger_limit_reason"] == "Automatic"
    assert changed_targets == set()
    # After expiration, the key should be set to None (cleared)
    assert coordinator._manual_overrides["charger_limit"] is None


def test_mixed_boolean_and_numeric_overrides(fake_hass, monkeypatch):
    """Ensure boolean and numeric overrides work together."""
    base_time = datetime(2025, 10, 27, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        coordinator_module.dt_util, "utcnow", lambda: base_time, raising=False
    )

    config = _base_config()
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    # Set both boolean and numeric overrides
    coordinator._manual_overrides["car_grid_charging"] = {
        "value": True,
        "reason": "Force charge",
        "expires_at": base_time + timedelta(minutes=60),
        "set_at": base_time,
    }
    coordinator._manual_overrides["charger_limit"] = {
        "value": 7000,
        "reason": "Limited power",
        "expires_at": base_time + timedelta(minutes=60),
        "set_at": base_time,
    }
    coordinator._manual_overrides["grid_setpoint"] = {
        "value": 10000,
        "reason": "High grid allowance",
        "expires_at": base_time + timedelta(minutes=60),
        "set_at": base_time,
    }

    decision = {
        "car_grid_charging": False,
        "car_grid_charging_reason": "Price too high",
        "charger_limit": 11000,
        "charger_limit_reason": "Default",
        "grid_setpoint": 6000,
        "grid_setpoint_reason": "Default",
        "strategy_trace": [],
    }

    updated_decision, changed_targets = coordinator._apply_manual_overrides(decision)

    # All overrides should be applied
    assert updated_decision["car_grid_charging"] is True
    assert updated_decision["charger_limit"] == 7000
    assert updated_decision["grid_setpoint"] == 10000
    assert changed_targets == {"car_grid_charging", "charger_limit", "grid_setpoint"}

    # Check overrides metadata
    overrides = updated_decision.get("manual_overrides", {})
    assert len(overrides) == 3
    assert "car_grid_charging" in overrides
    assert "charger_limit" in overrides
    assert "grid_setpoint" in overrides


def test_numeric_only_override_does_not_set_boolean(fake_hass, monkeypatch):
    """Ensure numeric-only overrides don't affect boolean charging decisions."""
    base_time = datetime(2025, 10, 27, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        coordinator_module.dt_util, "utcnow", lambda: base_time, raising=False
    )

    config = _base_config()
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    # Set charger_limit override with value=None (numeric-only)
    import asyncio

    asyncio.run(
        coordinator.async_set_manual_override(
            target="charger_limit",
            value=None,
            duration=timedelta(minutes=60),
            reason="Test limit",
            charger_limit=5000,
            grid_setpoint=None,
        )
    )

    # Verify that charger_limit is set
    assert "charger_limit" in coordinator._manual_overrides
    assert coordinator._manual_overrides["charger_limit"]["value"] == 5000

    # Verify that car_grid_charging is NOT set
    assert coordinator._manual_overrides.get("car_grid_charging") is None
    assert coordinator._manual_overrides.get("battery_grid_charging") is None

    # Apply overrides to a decision
    decision = {
        "car_grid_charging": False,
        "battery_grid_charging": False,
        "charger_limit": 11000,
        "strategy_trace": [],
    }

    updated_decision, changed_targets = coordinator._apply_manual_overrides(decision)

    # Charger limit should be overridden
    assert updated_decision["charger_limit"] == 5000
    assert "charger_limit" in changed_targets

    # Boolean values should NOT be changed
    assert updated_decision["car_grid_charging"] is False
    assert updated_decision["battery_grid_charging"] is False
    assert "car_grid_charging" not in changed_targets
    assert "battery_grid_charging" not in changed_targets


def test_mixed_boolean_target_applies_numeric_override_fields(fake_hass, monkeypatch):
    """Bundling numeric values with a boolean target should set both overrides.

    The dashboard "Force Car Charging" button prompts the user for charger_limit
    and grid_setpoint and sends them in the same ``target='car'`` service call.
    Those values must take effect (including ``grid_setpoint=0`` to suppress
    grid import while force-charging the car), not be silently dropped.
    """
    base_time = datetime(2025, 10, 27, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        coordinator_module.dt_util, "utcnow", lambda: base_time, raising=False
    )

    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)

    import asyncio

    asyncio.run(
        coordinator.async_set_manual_override(
            target="car",
            value=True,
            duration=timedelta(minutes=60),
            reason="Force car charging",
            charger_limit=5000,
            grid_setpoint=0,
        )
    )

    assert coordinator._manual_overrides["car_grid_charging"]["value"] is True
    assert coordinator._manual_overrides["charger_limit"]["value"] == 5000
    assert coordinator._manual_overrides["grid_setpoint"]["value"] == 0

    # Per-target clear scopes only to the named override.
    asyncio.run(coordinator.async_clear_manual_override("car"))

    assert coordinator._manual_overrides["car_grid_charging"] is None
    assert coordinator._manual_overrides["charger_limit"]["value"] == 5000
    assert coordinator._manual_overrides["grid_setpoint"]["value"] == 0


def test_force_car_with_zero_grid_setpoint_propagates_to_decision(
    fake_hass, monkeypatch
):
    """Force-charge car with ``grid_setpoint=0`` must clamp the decision to 0W."""
    base_time = datetime(2025, 10, 27, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        coordinator_module.dt_util, "utcnow", lambda: base_time, raising=False
    )

    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)

    import asyncio

    asyncio.run(
        coordinator.async_set_manual_override(
            target="car",
            value=True,
            duration=timedelta(minutes=60),
            reason="Solar-only car charging",
            grid_setpoint=0,
        )
    )

    decision = {
        "car_grid_charging": False,
        "car_grid_charging_reason": "Price too high",
        "battery_grid_charging": False,
        "grid_setpoint": 7000,
        "grid_setpoint_reason": "Default",
        "strategy_trace": [],
    }

    updated_decision, changed_targets = coordinator._apply_manual_overrides(decision)

    assert updated_decision["car_grid_charging"] is True
    assert updated_decision["grid_setpoint"] == 0
    assert "car_grid_charging" in changed_targets
    assert "grid_setpoint" in changed_targets


def test_clear_all_preserves_arbitrage_and_negative_buy_modes(fake_hass, monkeypatch):
    """``clear(target='all')`` must not disable persistent runtime mode toggles.

    Arbitrage Mode and Negative Arbitrage Buy Mode are persistent toggles owned
    by their dedicated dashboard switches.  The "Clear all" button is meant to
    cancel per-decision overrides only (battery/car/charger_limit/grid_setpoint).
    """
    base_time = datetime(2025, 10, 27, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        coordinator_module.dt_util, "utcnow", lambda: base_time, raising=False
    )

    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)

    coordinator._arbitrage_mode_state = {
        "value": True,
        "reason": "Arbitrage mode enabled",
        "expires_at": None,
        "set_at": base_time,
    }
    coordinator._negative_buy_mode_state = {
        "value": True,
        "reason": "Negative buy mode enabled",
        "expires_at": None,
        "set_at": base_time,
    }
    coordinator._manual_overrides["car_grid_charging"] = {
        "value": True,
        "reason": "Force charge",
        "expires_at": base_time + timedelta(minutes=60),
        "set_at": base_time,
    }
    coordinator._manual_overrides["grid_setpoint"] = {
        "value": 0,
        "reason": "Solar-only",
        "expires_at": base_time + timedelta(minutes=60),
        "set_at": base_time,
    }

    import asyncio

    asyncio.run(coordinator.async_clear_manual_override("all"))

    # Per-decision overrides cleared.
    assert coordinator._manual_overrides["car_grid_charging"] is None
    assert coordinator._manual_overrides["grid_setpoint"] is None

    # Persistent runtime mode toggles untouched.
    assert coordinator._arbitrage_mode_state["value"] is True
    assert coordinator._negative_buy_mode_state["value"] is True
    assert "arbitrage_mode" not in coordinator._manual_overrides
    assert "negative_buy_mode" not in coordinator._manual_overrides


def test_manual_override_target_mapping_excludes_runtime_modes(fake_hass, monkeypatch):
    """Runtime mode toggles are not manual override clear targets."""
    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)

    assert coordinator._manual_override_manager.resolve_targets("arbitrage_mode") == ()
    assert coordinator._manual_override_manager.resolve_targets("negative_buy") == ()
    assert set(coordinator._manual_override_manager.resolve_targets("all")) == {
        "battery_grid_charging",
        "car_grid_charging",
        "charger_limit",
        "grid_setpoint",
    }


def test_runtime_modes_are_not_exposed_as_manual_override_status(
    fake_hass, monkeypatch
):
    """Runtime mode toggles should not appear in the decision manual override payload."""
    base_time = datetime(2025, 10, 27, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        coordinator_module.dt_util, "utcnow", lambda: base_time, raising=False
    )

    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)

    coordinator._arbitrage_mode_state = {
        "value": True,
        "reason": "Manual arbitrage mode via dashboard switch",
        "expires_at": None,
        "set_at": base_time,
    }
    coordinator._negative_buy_mode_state = {
        "value": True,
        "reason": "Manual Negative Arbitrage Buy mode via dashboard switch",
        "expires_at": None,
        "set_at": base_time,
    }

    decision = {
        "battery_grid_charging": False,
        "car_grid_charging": False,
        "manual_overrides": {
            "arbitrage_mode": {"value": True},
            "negative_buy_mode": {"value": True},
        },
        "strategy_trace": [],
    }

    updated_decision, changed_targets = coordinator._apply_manual_overrides(decision)

    assert changed_targets == set()
    assert updated_decision["manual_overrides"] == {}
