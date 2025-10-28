"""Tests for peak threshold calculation in coordinator."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.electricity_planner import coordinator as coordinator_module
from custom_components.electricity_planner.coordinator import ElectricityPlannerCoordinator
from custom_components.electricity_planner.const import (
    CONF_BATTERY_SOC_ENTITIES,
    CONF_BASE_GRID_SETPOINT,
    CONF_CURRENT_PRICE_ENTITY,
    CONF_GRID_POWER_ENTITY,
    CONF_HIGHEST_PRICE_ENTITY,
    CONF_LOWEST_PRICE_ENTITY,
    CONF_MONTHLY_GRID_PEAK_ENTITY,
    CONF_NEXT_PRICE_ENTITY,
    CONF_CAR_CHARGING_POWER_ENTITY,
    CONF_MIN_CAR_CHARGING_THRESHOLD,
    DOMAIN,
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


def _base_config():
    return {
        CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
        CONF_HIGHEST_PRICE_ENTITY: "sensor.highest_price",
        CONF_LOWEST_PRICE_ENTITY: "sensor.lowest_price",
        CONF_NEXT_PRICE_ENTITY: "sensor.next_price",
        CONF_BATTERY_SOC_ENTITIES: ["sensor.battery_soc"],
        CONF_BASE_GRID_SETPOINT: 5000,
        CONF_MIN_CAR_CHARGING_THRESHOLD: 100,
    }


def _create_coordinator(fake_hass, config, monkeypatch):
    entry = MockConfigEntry(domain=DOMAIN, data=config, options={})
    monkeypatch.setattr(
        coordinator_module.ElectricityPlannerCoordinator,
        "_setup_entity_listeners",
        lambda self: None,
    )
    coordinator = ElectricityPlannerCoordinator(fake_hass, entry)
    return coordinator


@pytest.fixture
def fake_hass():
    return FakeHass()


def _freeze_time(monkeypatch, base_time):
    monkeypatch.setattr(coordinator_module.dt_util, "now", lambda: base_time, raising=False)
    monkeypatch.setattr(coordinator_module.dt_util, "utcnow", lambda: base_time, raising=False)


@pytest.mark.parametrize(
    "monthly_peak,base_setpoint,expected_threshold",
    [
        # Case 1: Normal - monthly peak exceeds base
        (6000, 5000, 6300),  # 6000 * 1.05 = 6300W

        # Case 2: Low usage month - monthly peak below base
        (4000, 5000, 5250),  # max(4000, 5000) * 1.05 = 5250W

        # Case 3: No monthly peak sensor (None)
        (None, 5000, 5250),  # max(0, 5000) * 1.05 = 5250W

        # Case 4: High usage month
        (7000, 5000, 7350),  # 7000 * 1.05 = 7350W

        # Case 5: Monthly peak equals base
        (5000, 5000, 5250),  # 5000 * 1.05 = 5250W

        # Case 6: Monthly peak zero
        (0, 5000, 5250),  # max(0, 5000) * 1.05 = 5250W
    ],
)
def test_peak_threshold_calculation(
    fake_hass, monkeypatch, monthly_peak, base_setpoint, expected_threshold
):
    """Test that peak threshold is correctly calculated as 105% of effective peak."""
    base_time = datetime(2025, 10, 27, 12, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    config[CONF_BASE_GRID_SETPOINT] = base_setpoint
    config[CONF_GRID_POWER_ENTITY] = "sensor.grid_power"
    config[CONF_MONTHLY_GRID_PEAK_ENTITY] = "sensor.monthly_peak"
    config[CONF_CAR_CHARGING_POWER_ENTITY] = "sensor.car_power"

    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    # Prepare test data
    data = {
        "monthly_grid_peak": monthly_peak,
        "grid_power": 3000.0,  # Importing from grid (positive = import)
        "car_charging_power": 2000.0,  # Car actively charging
    }

    # Execute the method
    coordinator._update_peak_limit_state(data)

    # Verify the threshold
    assert data["car_peak_limit_threshold"] == pytest.approx(expected_threshold, rel=1e-6)


def test_peak_threshold_uses_max_of_monthly_and_base(fake_hass, monkeypatch):
    """Test that effective peak is max(monthly_peak, base_setpoint)."""
    base_time = datetime(2025, 10, 27, 12, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    config[CONF_BASE_GRID_SETPOINT] = 5000
    config[CONF_GRID_POWER_ENTITY] = "sensor.grid_power"
    config[CONF_MONTHLY_GRID_PEAK_ENTITY] = "sensor.monthly_peak"
    config[CONF_CAR_CHARGING_POWER_ENTITY] = "sensor.car_power"

    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    # Test Case A: monthly_peak > base_setpoint
    data_a = {
        "monthly_grid_peak": 6500.0,
        "grid_power": 3000.0,  # Importing (positive = import)
        "car_charging_power": 2000.0,
    }
    coordinator._update_peak_limit_state(data_a)
    # Effective peak = max(6500, 5000) = 6500
    # Threshold = 6500 * 1.05 = 6825
    assert data_a["car_peak_limit_threshold"] == pytest.approx(6825.0, rel=1e-6)

    # Test Case B: monthly_peak < base_setpoint
    data_b = {
        "monthly_grid_peak": 3500.0,
        "grid_power": 3000.0,  # Importing (positive = import)
        "car_charging_power": 2000.0,
    }
    coordinator._update_peak_limit_state(data_b)
    # Effective peak = max(3500, 5000) = 5000
    # Threshold = 5000 * 1.05 = 5250
    assert data_b["car_peak_limit_threshold"] == pytest.approx(5250.0, rel=1e-6)


def test_peak_monitoring_starts_when_exceeding_threshold(fake_hass, monkeypatch):
    """Test that monitoring starts when grid import exceeds threshold."""
    base_time = datetime(2025, 10, 27, 12, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    config[CONF_BASE_GRID_SETPOINT] = 5000
    config[CONF_GRID_POWER_ENTITY] = "sensor.grid_power"
    config[CONF_CAR_CHARGING_POWER_ENTITY] = "sensor.car_power"

    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    # Monthly peak = 6000W → threshold = 6300W
    data = {
        "monthly_grid_peak": 6000.0,
        "grid_power": 6500.0,  # Importing 6500W (exceeds threshold, positive = import)
        "car_charging_power": 3000.0,  # Car actively charging
    }

    coordinator._update_peak_limit_state(data)

    # Monitoring should have started
    assert coordinator._car_peak_limit_started_at == base_time
    assert data["car_peak_limited"] is False  # Not limited yet (need 5 min)


def test_peak_limit_triggers_after_5_minutes(fake_hass, monkeypatch):
    """Test that peak limit triggers after 5 minutes of sustained exceedance."""
    base_time = datetime(2025, 10, 27, 12, 0, tzinfo=timezone.utc)
    clock = {"now": base_time}

    def mock_utcnow():
        return clock["now"]

    monkeypatch.setattr(coordinator_module.dt_util, "utcnow", mock_utcnow, raising=False)

    config = _base_config()
    config[CONF_BASE_GRID_SETPOINT] = 5000
    config[CONF_GRID_POWER_ENTITY] = "sensor.grid_power"
    config[CONF_CAR_CHARGING_POWER_ENTITY] = "sensor.car_power"

    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    data = {
        "monthly_grid_peak": 6000.0,
        "grid_power": 6500.0,  # Exceeds 6300W threshold (positive = import)
        "car_charging_power": 3000.0,
    }

    # First call - start monitoring
    coordinator._update_peak_limit_state(data)
    assert coordinator._car_peak_limit_started_at == base_time
    assert data["car_peak_limited"] is False

    # After 3 minutes - still monitoring
    clock["now"] = base_time + timedelta(minutes=3)
    coordinator._update_peak_limit_state(data)
    assert coordinator._car_peak_limit_started_at == base_time
    assert data["car_peak_limited"] is False

    # After 5 minutes - should trigger limit
    clock["now"] = base_time + timedelta(minutes=5)
    coordinator._update_peak_limit_state(data)
    assert coordinator._car_peak_limit_started_at is None  # Reset
    assert data["car_peak_limited"] is True
    assert coordinator._car_peak_limited_until == base_time + timedelta(minutes=20)


def test_peak_monitoring_resets_when_below_threshold(fake_hass, monkeypatch):
    """Test that monitoring resets if import drops below threshold before 5 minutes."""
    base_time = datetime(2025, 10, 27, 12, 0, tzinfo=timezone.utc)
    clock = {"now": base_time}

    def mock_utcnow():
        return clock["now"]

    monkeypatch.setattr(coordinator_module.dt_util, "utcnow", mock_utcnow, raising=False)

    config = _base_config()
    config[CONF_BASE_GRID_SETPOINT] = 5000
    config[CONF_GRID_POWER_ENTITY] = "sensor.grid_power"
    config[CONF_CAR_CHARGING_POWER_ENTITY] = "sensor.car_power"

    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    # Start exceeding threshold
    data = {
        "monthly_grid_peak": 6000.0,
        "grid_power": 6500.0,  # Exceeds 6300W threshold (positive = import)
        "car_charging_power": 3000.0,
    }
    coordinator._update_peak_limit_state(data)
    assert coordinator._car_peak_limit_started_at == base_time

    # After 2 minutes, drop below threshold
    clock["now"] = base_time + timedelta(minutes=2)
    data["grid_power"] = 5000.0  # Below 6300W threshold (positive = import)
    coordinator._update_peak_limit_state(data)

    # Monitoring should reset
    assert coordinator._car_peak_limit_started_at is None
    assert data["car_peak_limited"] is False


def test_peak_limit_expires_after_15_minutes(fake_hass, monkeypatch):
    """Test that peak limit automatically expires after 15 minutes."""
    base_time = datetime(2025, 10, 27, 12, 0, tzinfo=timezone.utc)
    clock = {"now": base_time}

    def mock_utcnow():
        return clock["now"]

    monkeypatch.setattr(coordinator_module.dt_util, "utcnow", mock_utcnow, raising=False)

    config = _base_config()
    config[CONF_BASE_GRID_SETPOINT] = 5000
    config[CONF_GRID_POWER_ENTITY] = "sensor.grid_power"
    config[CONF_CAR_CHARGING_POWER_ENTITY] = "sensor.car_power"

    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    # Manually set limit (simulating triggered state)
    coordinator._car_peak_limited_until = base_time + timedelta(minutes=15)

    data = {
        "monthly_grid_peak": 6000.0,
        "grid_power": -6500.0,
        "car_charging_power": 3000.0,
    }

    # At 14 minutes - still limited
    clock["now"] = base_time + timedelta(minutes=14)
    coordinator._update_peak_limit_state(data)
    assert data["car_peak_limited"] is True

    # At 15 minutes - limit expired
    clock["now"] = base_time + timedelta(minutes=15)
    coordinator._update_peak_limit_state(data)
    assert data["car_peak_limited"] is False
    assert coordinator._car_peak_limited_until is None


def test_no_monitoring_when_car_not_charging(fake_hass, monkeypatch):
    """Test that monitoring doesn't start if car is not actively charging."""
    base_time = datetime(2025, 10, 27, 12, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    config[CONF_BASE_GRID_SETPOINT] = 5000
    config[CONF_GRID_POWER_ENTITY] = "sensor.grid_power"
    config[CONF_CAR_CHARGING_POWER_ENTITY] = "sensor.car_power"

    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    data = {
        "monthly_grid_peak": 6000.0,
        "grid_power": 6500.0,  # Exceeds threshold (positive = import)
        "car_charging_power": 50.0,  # Below min threshold (100W)
    }

    coordinator._update_peak_limit_state(data)

    # Monitoring should not start
    assert coordinator._car_peak_limit_started_at is None
    assert data["car_peak_limited"] is False
