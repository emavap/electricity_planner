"""Integration-oriented tests for Electricity Planner coordinator."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytz
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.electricity_planner import coordinator as coordinator_module
from custom_components.electricity_planner import energy_ledger as energy_ledger_module
from custom_components.electricity_planner.const import (
    ATTR_ACTION,
    ATTR_ENTRY_ID,
    ATTR_TARGET,
    CONF_ARBITRAGE_MODE_DEADLINE_HOUR,
    CONF_ARBITRAGE_MODE_MAX_EXPORT_POWER,
    CONF_ARBITRAGE_MODE_RESERVE_SOC,
    CONF_BASE_GRID_SETPOINT,
    CONF_BATTERY_CAPACITIES,
    CONF_BATTERY_PHASE_ASSIGNMENTS,
    CONF_BATTERY_SOC_ENTITIES,
    CONF_BUY_VAT_MULTIPLIER,
    CONF_CAR_CHARGING_POWER_ENTITY,
    CONF_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
    CONF_CURRENT_PRICE_ENTITY,
    CONF_DYNAMIC_THRESHOLD_CONFIDENCE,
    CONF_GRID_IMPORT_NEGATIVE_FEEDBACK,
    CONF_GRID_POWER_ENTITY,
    CONF_HIGHEST_PRICE_ENTITY,
    CONF_HOUSE_CONSUMPTION_ENTITY,
    CONF_LOWEST_PRICE_ENTITY,
    CONF_MAX_SOC_THRESHOLD,
    CONF_MONTHLY_GRID_PEAK_ENTITY,
    CONF_NEGATIVE_BUY_THRESHOLD,
    CONF_NEXT_PRICE_ENTITY,
    CONF_P1_TARIFF_ENTITY,
    CONF_PHASE_BATTERY_POWER_ENTITY,
    CONF_PHASE_CAR_ENTITY,
    CONF_PHASE_CONSUMPTION_ENTITY,
    CONF_PHASE_GRID_POWER_ENTITY,
    CONF_PHASE_MODE,
    CONF_PHASE_SOLAR_ENTITY,
    CONF_PHASES,
    CONF_PRICE_THRESHOLD,
    CONF_SOLAR_FORECAST_ENTITY_TOMORROW,
    CONF_SOLAR_FORECAST_START_HOUR,
    CONF_SOLAR_FORECAST_TODAY_ENTITY,
    CONF_SOLAR_PRODUCTION_ENTITY,
    CONF_TRANSPORT_COST_DAY,
    CONF_TRANSPORT_COST_ENTITY,
    CONF_TRANSPORT_COST_NIGHT,
    CONF_USE_AVERAGE_THRESHOLD,
    DEFAULT_BUY_VAT_MULTIPLIER,
    DEFAULT_ENERGY_COST_GSC,
    DEFAULT_ENERGY_COST_WKK,
    DEFAULT_ENERGY_TAX_ACCIJNS,
    DEFAULT_ENERGY_TAX_BIJDRAGE,
    DEFAULT_TRANSPORT_COST_DAY,
    DEFAULT_TRANSPORT_COST_NIGHT,
    DOMAIN,
    MANUAL_OVERRIDE_ACTION_FORCE_CHARGE,
    MANUAL_OVERRIDE_TARGET_BATTERY,
    MANUAL_OVERRIDE_TARGET_CAR,
    PHASE_MODE_THREE,
    SERVICE_CLEAR_MANUAL_OVERRIDE,
    SERVICE_SET_MANUAL_OVERRIDE,
)
from custom_components.electricity_planner.coordinator import (
    ElectricityPlannerCoordinator,
)
from custom_components.electricity_planner.energy_ledger import (
    BatteryEnergyLedger,
    EnergyOrigin,
)
from custom_components.electricity_planner.sensor import GridSetpointSensor


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


class FakeServices:
    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []
        self.registered: dict[tuple[str, str], dict] = {}

    async def async_call(self, domain, service, data, blocking=False, context=None):
        self.calls.append((domain, service, data))

    def async_register(self, domain, service, handler, schema=None):
        self.registered[(domain, service)] = {
            "handler": handler,
            "schema": schema,
        }


class FakeHass:
    def __init__(self):
        self.states = FakeStates()
        self.services = FakeServices()
        self.data: dict = {}
        self.config_entries = SimpleNamespace(_entries={})

    def async_create_task(self, coro):
        return asyncio.get_running_loop().create_task(coro)

    async def async_add_executor_job(self, func, *args, **kwargs):
        return func(*args, **kwargs)


@pytest.fixture
def fake_hass():
    return FakeHass()


def _base_config():
    return {
        CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
        CONF_HIGHEST_PRICE_ENTITY: "sensor.highest_price",
        CONF_LOWEST_PRICE_ENTITY: "sensor.lowest_price",
        CONF_NEXT_PRICE_ENTITY: "sensor.next_price",
        "price_adjustment_multiplier": 1.0,
        "price_adjustment_offset": 0.0,
        CONF_BATTERY_SOC_ENTITIES: ["sensor.battery_soc_1", "sensor.battery_soc_2"],
        CONF_SOLAR_PRODUCTION_ENTITY: "sensor.solar_production",
        CONF_HOUSE_CONSUMPTION_ENTITY: "sensor.house_consumption",
        CONF_CAR_CHARGING_POWER_ENTITY: "sensor.car_power",
    }


def _three_phase_config():
    config = _base_config()
    config.pop(CONF_SOLAR_PRODUCTION_ENTITY, None)
    config.pop(CONF_HOUSE_CONSUMPTION_ENTITY, None)
    config.pop(CONF_CAR_CHARGING_POWER_ENTITY, None)
    config.update(
        {
            CONF_PHASE_MODE: PHASE_MODE_THREE,
            CONF_PHASES: {
                "phase_1": {
                    CONF_PHASE_SOLAR_ENTITY: "sensor.solar_l1",
                    CONF_PHASE_CONSUMPTION_ENTITY: "sensor.load_l1",
                    CONF_PHASE_GRID_POWER_ENTITY: "sensor.grid_l1",
                },
                "phase_2": {
                    CONF_PHASE_SOLAR_ENTITY: "sensor.solar_l2",
                    CONF_PHASE_CONSUMPTION_ENTITY: "sensor.load_l2",
                    CONF_PHASE_CAR_ENTITY: "sensor.car_l2",
                    CONF_PHASE_GRID_POWER_ENTITY: "sensor.grid_l2",
                },
                "phase_3": {
                    CONF_PHASE_SOLAR_ENTITY: "sensor.solar_l3",
                    CONF_PHASE_CONSUMPTION_ENTITY: "sensor.load_l3",
                },
            },
            CONF_BATTERY_CAPACITIES: {
                "sensor.battery_soc_1": 10.0,
                "sensor.battery_soc_2": 6.0,
            },
            CONF_BATTERY_PHASE_ASSIGNMENTS: {
                "sensor.battery_soc_1": ["phase_1", "phase_2"],
                "sensor.battery_soc_2": ["phase_2"],
            },
        }
    )
    return config


def _create_coordinator(fake_hass, config, monkeypatch, options=None):
    entry = MockConfigEntry(domain=DOMAIN, data=config, options=options or {})
    monkeypatch.setattr(
        coordinator_module.ElectricityPlannerCoordinator,
        "_setup_entity_listeners",
        lambda self: None,
    )
    coordinator = ElectricityPlannerCoordinator(fake_hass, entry)
    return coordinator


def test_coordinator_merges_entry_options(fake_hass, monkeypatch):
    config = _base_config()
    config[CONF_PRICE_THRESHOLD] = 0.12
    options = {
        CONF_PRICE_THRESHOLD: 0.05,
        CONF_DYNAMIC_THRESHOLD_CONFIDENCE: 80,
    }
    coordinator = _create_coordinator(fake_hass, config, monkeypatch, options)

    assert coordinator.config[CONF_PRICE_THRESHOLD] == 0.05
    assert coordinator.config[CONF_DYNAMIC_THRESHOLD_CONFIDENCE] == 80


def test_battery_state_tracking_uses_automatic_decisions(fake_hass, monkeypatch):
    base_time = datetime(2025, 6, 1, 6, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)
    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)

    coordinator._update_battery_charging_state_tracking(
        True, set(), effective_threshold=0.18
    )

    assert coordinator._previous_battery_grid_charging is True
    assert coordinator._battery_grid_charging_changed_at == base_time
    assert coordinator._battery_grid_charging_locked_threshold == 0.18


def test_battery_state_tracking_ignores_manual_battery_override(fake_hass, monkeypatch):
    base_time = datetime(2025, 6, 1, 6, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)
    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    coordinator._previous_battery_grid_charging = True
    coordinator._battery_grid_charging_changed_at = base_time - timedelta(minutes=1)
    coordinator._battery_grid_charging_locked_threshold = 0.18

    coordinator._update_battery_charging_state_tracking(
        True,
        {"battery_grid_charging"},
    )

    assert coordinator._previous_battery_grid_charging is False
    assert coordinator._battery_grid_charging_changed_at is None
    assert coordinator._battery_grid_charging_locked_threshold is None


def test_battery_state_tracking_clears_lock_on_off_transition(fake_hass, monkeypatch):
    base_time = datetime(2025, 6, 1, 6, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)
    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    coordinator._previous_battery_grid_charging = True
    coordinator._battery_grid_charging_changed_at = base_time - timedelta(minutes=10)
    coordinator._battery_grid_charging_locked_threshold = 0.18

    coordinator._update_battery_charging_state_tracking(False, set())

    assert coordinator._previous_battery_grid_charging is False
    assert coordinator._battery_grid_charging_changed_at == base_time
    assert coordinator._battery_grid_charging_locked_threshold is None


def test_battery_state_tracking_keeps_lock_while_state_unchanged(
    fake_hass, monkeypatch
):
    base_time = datetime(2025, 6, 1, 6, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)
    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    coordinator._previous_battery_grid_charging = True
    coordinator._battery_grid_charging_changed_at = base_time - timedelta(minutes=2)
    coordinator._battery_grid_charging_locked_threshold = 0.18

    coordinator._update_battery_charging_state_tracking(
        True, set(), effective_threshold=0.12
    )

    # No state flip → lock and timestamp must be preserved (not re-captured at the
    # current, lower effective threshold).
    assert coordinator._battery_grid_charging_locked_threshold == 0.18
    assert coordinator._battery_grid_charging_changed_at == base_time - timedelta(
        minutes=2
    )


def test_grid_setpoint_sensor_uses_configured_base_setpoint_in_attributes(
    fake_hass, monkeypatch
):
    config = _base_config()
    config[CONF_BASE_GRID_SETPOINT] = 4200
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)
    coordinator.data = {
        "grid_setpoint": 4200,
        "grid_setpoint_reason": "Automatic",
        "monthly_grid_peak": 0,
        "power_analysis": {},
        "battery_analysis": {},
    }

    entry = MockConfigEntry(domain=DOMAIN, data=config, options={})
    sensor = GridSetpointSensor(coordinator, entry)
    attributes = sensor.extra_state_attributes

    assert attributes["base_grid_setpoint"] == 4200
    assert attributes["controlling_peak"] == 4200
    assert attributes["peak_based_grid_setpoint"] == 3780
    assert attributes["max_grid_setpoint"] == 3780


@pytest.mark.asyncio
async def test_fetch_all_data_computes_surplus_and_filters_unavailable(
    fake_hass, monkeypatch
):
    config = _base_config()
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    fake_hass.states.set("sensor.current_price", "0.12")
    fake_hass.states.set("sensor.highest_price", "0.30")
    fake_hass.states.set("sensor.lowest_price", "0.05")
    fake_hass.states.set("sensor.next_price", "0.10")
    fake_hass.states.set("sensor.battery_soc_1", "45")
    fake_hass.states.set("sensor.battery_soc_2", "unknown")
    fake_hass.states.set("sensor.solar_production", "4200")
    fake_hass.states.set("sensor.house_consumption", "3200")
    fake_hass.states.set("sensor.car_power", "1400")

    data = await coordinator._fetch_all_data()

    assert data["solar_surplus"] == 1000
    assert data["battery_soc"] == [{"entity_id": "sensor.battery_soc_1", "soc": 45.0}]
    assert data["car_charging_power"] == 1400.0


@pytest.mark.asyncio
async def test_fetch_all_data_three_phase_aggregates(fake_hass, monkeypatch):
    config = _three_phase_config()
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    # Price sensors
    fake_hass.states.set("sensor.current_price", "0.15")
    fake_hass.states.set("sensor.highest_price", "0.32")
    fake_hass.states.set("sensor.lowest_price", "0.05")
    fake_hass.states.set("sensor.next_price", "0.11")

    # Battery SOC sensors
    fake_hass.states.set("sensor.battery_soc_1", "40")
    fake_hass.states.set("sensor.battery_soc_2", "60")

    # Phase-specific sensors
    fake_hass.states.set("sensor.solar_l1", "1200")
    fake_hass.states.set("sensor.load_l1", "800")
    fake_hass.states.set("sensor.grid_l1", "-250")
    fake_hass.states.set("sensor.solar_l2", "600")
    fake_hass.states.set("sensor.load_l2", "900")
    fake_hass.states.set("sensor.car_l2", "700")
    fake_hass.states.set("sensor.grid_l2", "1050")
    fake_hass.states.set("sensor.solar_l3", "300")
    fake_hass.states.set("sensor.load_l3", "200")

    data = await coordinator._fetch_all_data()

    assert data["phase_mode"] == PHASE_MODE_THREE
    assert data["solar_production"] == pytest.approx(2100.0)
    assert data["house_consumption"] == pytest.approx(1900.0)
    assert data["solar_surplus"] == pytest.approx(200.0)
    assert data["car_charging_power"] == pytest.approx(700.0)

    phase_details = data["phase_details"]
    assert phase_details["phase_1"]["solar_surplus"] == pytest.approx(400.0)
    assert phase_details["phase_1"]["grid_power"] == pytest.approx(-250.0)
    assert phase_details["phase_1"]["has_grid_power_sensor"] is True
    assert phase_details["phase_2"]["solar_surplus"] == 0
    assert phase_details["phase_2"]["car_charging_power"] == pytest.approx(700.0)
    assert phase_details["phase_2"]["grid_power"] == pytest.approx(1050.0)
    assert phase_details["phase_2"]["has_grid_power_sensor"] is True
    assert phase_details["phase_3"]["grid_power"] is None
    assert phase_details["phase_3"]["has_grid_power_sensor"] is False

    capacity_map = data["phase_capacity_map"]
    assert capacity_map["phase_1"] == pytest.approx(5.0)
    assert capacity_map["phase_2"] == pytest.approx(11.0)
    assert capacity_map["phase_3"] == pytest.approx(0.0)

    phase_batteries = data["phase_batteries"]
    assert [b["entity_id"] for b in phase_batteries["phase_1"]] == [
        "sensor.battery_soc_1"
    ]
    assert [b["entity_id"] for b in phase_batteries["phase_2"]] == [
        "sensor.battery_soc_1",
        "sensor.battery_soc_2",
    ]

    battery_1 = next(
        b for b in data["battery_details"] if b["entity_id"] == "sensor.battery_soc_1"
    )
    assert battery_1["capacity"] == pytest.approx(10.0)
    assert battery_1["phases"] == ["phase_1", "phase_2"]


@pytest.mark.asyncio
async def test_fetch_all_data_three_phase_with_battery_power_sensors(
    fake_hass, monkeypatch
):
    """Test that battery power sensors are correctly read in three-phase mode."""
    config = _three_phase_config()
    # Add battery power sensors to phase configuration
    config[CONF_PHASES]["phase_1"][
        CONF_PHASE_BATTERY_POWER_ENTITY
    ] = "sensor.battery_power_l1"
    config[CONF_PHASES]["phase_2"][
        CONF_PHASE_BATTERY_POWER_ENTITY
    ] = "sensor.battery_power_l2"

    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    # Price sensors
    fake_hass.states.set("sensor.current_price", "0.15")
    fake_hass.states.set("sensor.highest_price", "0.32")
    fake_hass.states.set("sensor.lowest_price", "0.05")
    fake_hass.states.set("sensor.next_price", "0.11")

    # Battery SOC sensors
    fake_hass.states.set("sensor.battery_soc_1", "40")
    fake_hass.states.set("sensor.battery_soc_2", "60")

    # Phase-specific sensors
    fake_hass.states.set("sensor.solar_l1", "1200")
    fake_hass.states.set("sensor.load_l1", "800")
    fake_hass.states.set("sensor.battery_power_l1", "-500")  # Negative = charging
    fake_hass.states.set("sensor.solar_l2", "600")
    fake_hass.states.set("sensor.load_l2", "900")
    fake_hass.states.set("sensor.car_l2", "700")
    fake_hass.states.set("sensor.battery_power_l2", "300")  # Positive = discharging
    fake_hass.states.set("sensor.solar_l3", "300")
    fake_hass.states.set("sensor.load_l3", "200")

    data = await coordinator._fetch_all_data()

    assert data["phase_mode"] == PHASE_MODE_THREE

    phase_details = data["phase_details"]
    assert phase_details["phase_1"]["battery_power"] == pytest.approx(-500.0)
    assert phase_details["phase_1"]["has_battery_power_sensor"] is True
    assert phase_details["phase_2"]["battery_power"] == pytest.approx(300.0)
    assert phase_details["phase_2"]["has_battery_power_sensor"] is True
    assert phase_details["phase_3"]["battery_power"] is None
    assert phase_details["phase_3"]["has_battery_power_sensor"] is False


@pytest.mark.asyncio
async def test_three_phase_falls_back_to_global_car_sensor(fake_hass, monkeypatch):
    """When no per-phase car sensors exist, fall back to aggregated car power entity."""
    config = _three_phase_config()
    for phase_config in config[CONF_PHASES].values():
        phase_config.pop(CONF_PHASE_CAR_ENTITY, None)
    config[CONF_CAR_CHARGING_POWER_ENTITY] = "sensor.car_power"

    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    # Price sensors (minimal required for fetch)
    fake_hass.states.set("sensor.current_price", "0.15")
    fake_hass.states.set("sensor.highest_price", "0.32")
    fake_hass.states.set("sensor.lowest_price", "0.05")
    fake_hass.states.set("sensor.next_price", "0.11")

    # Battery SOC sensors
    fake_hass.states.set("sensor.battery_soc_1", "55")
    fake_hass.states.set("sensor.battery_soc_2", "65")

    # Phase-specific sensors without car entities
    fake_hass.states.set("sensor.solar_l1", "1000")
    fake_hass.states.set("sensor.load_l1", "800")
    fake_hass.states.set("sensor.solar_l2", "900")
    fake_hass.states.set("sensor.load_l2", "700")
    fake_hass.states.set("sensor.solar_l3", "600")
    fake_hass.states.set("sensor.load_l3", "500")

    # Aggregated car power sensor should be used
    fake_hass.states.set("sensor.car_power", "1800")

    data = await coordinator._fetch_all_data()

    assert data["car_charging_power"] == pytest.approx(1800.0)
    for phase_id, details in data["phase_details"].items():
        assert details["car_charging_power"] is None
        assert details["has_car_sensor"] is False


@pytest.mark.asyncio
async def test_async_update_data_merges_decisions(fake_hass, monkeypatch):
    config = _base_config()
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    coordinator._fetch_all_data = AsyncMock(
        return_value={
            "current_price": 0.11,
            "highest_price": 0.2,
            "lowest_price": 0.05,
        }
    )
    coordinator.decision_engine.evaluate_charging_decision = AsyncMock(
        return_value={
            "battery_grid_charging": True,
            "price_analysis": {"data_available": True},
        }
    )
    coordinator._check_data_availability = AsyncMock()

    result = await coordinator._async_update_data()

    assert result["battery_grid_charging"] is True
    coordinator._fetch_all_data.assert_awaited_once()
    coordinator.decision_engine.evaluate_charging_decision.assert_awaited_once()
    coordinator._check_data_availability.assert_awaited_once()


@pytest.mark.asyncio
async def test_car_off_clears_grid_tracking_and_next_on_starts_fresh_window(
    fake_hass, monkeypatch
):
    """An Off decision must make a later On independent of old P1 samples."""
    base_time = datetime(2026, 8, 2, 19, 0, tzinfo=timezone.utc)
    now_ref = {"value": base_time}
    monkeypatch.setattr(
        coordinator_module.dt_util, "utcnow", lambda: now_ref["value"], raising=False
    )
    config = _base_config()
    config[CONF_GRID_IMPORT_NEGATIVE_FEEDBACK] = True
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)
    coordinator._fetch_all_data = AsyncMock(
        return_value={
            "current_price": 0.11,
            "highest_price": 0.2,
            "lowest_price": 0.05,
            "grid_power": -2000,
        }
    )
    coordinator._check_data_availability = AsyncMock()
    coordinator._previous_car_charging = True
    coordinator._grid_tracking_charger_limit = 1800
    coordinator._grid_tracking_last_adjustment = base_time
    coordinator._grid_tracking_window_started_at = base_time - timedelta(minutes=3)
    coordinator._grid_tracking_power_history.append((base_time, -2500.0))
    coordinator.decision_engine.evaluate_charging_decision = AsyncMock(
        side_effect=[
            {"battery_grid_charging": False, "car_grid_charging": False},
            {"battery_grid_charging": False, "car_grid_charging": True},
        ]
    )

    await coordinator._async_update_data()

    assert coordinator._grid_tracking_charger_limit is None
    assert coordinator._grid_tracking_last_adjustment is None
    assert not coordinator._grid_tracking_power_history
    assert coordinator._grid_tracking_window_started_at is None

    now_ref["value"] += timedelta(seconds=1)
    await coordinator._async_update_data()

    assert coordinator._grid_tracking_window_started_at == now_ref["value"]
    assert list(coordinator._grid_tracking_power_history) == [
        (now_ref["value"], -2000.0)
    ]


def test_grid_tracking_average_is_time_weighted():
    """Uneven P1 update timing must not bias the three-minute feedback window."""
    start = datetime(2026, 8, 2, 19, 0, tzinfo=timezone.utc)
    average = ElectricityPlannerCoordinator._time_weighted_power_average(
        [
            (start, -1000.0),
            (start + timedelta(seconds=10), -4000.0),
        ],
        start,
        start + timedelta(seconds=180),
    )

    assert average == pytest.approx((-1000 * 10 + -4000 * 170) / 180)


@pytest.mark.asyncio
async def test_data_unavailability_triggers_notifications(fake_hass, monkeypatch):
    config = _base_config()
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    send_notification = AsyncMock()
    monkeypatch.setattr(coordinator, "_send_notification", send_notification)

    base_time = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    now_ref = {"value": base_time}
    monkeypatch.setattr(
        coordinator_module.dt_util, "utcnow", lambda: now_ref["value"], raising=False
    )

    unavailable = {"current_price": None, "price_analysis": {}}

    await coordinator._check_data_availability(unavailable)
    assert coordinator.data_unavailable_since == base_time
    send_notification.assert_not_called()

    now_ref["value"] = base_time + timedelta(seconds=70)
    await coordinator._check_data_availability(unavailable)
    send_notification.assert_awaited_once()
    assert coordinator.notification_sent is True


@pytest.mark.asyncio
async def test_short_outage_does_not_emit_restored_notification(fake_hass, monkeypatch):
    """Short blips should not generate a misleading recovery notification."""
    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)

    send_notification = AsyncMock()
    monkeypatch.setattr(coordinator, "_send_notification", send_notification)

    base_time = datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc)
    now_ref = {"value": base_time}
    monkeypatch.setattr(
        coordinator_module.dt_util, "utcnow", lambda: now_ref["value"], raising=False
    )

    unavailable = {"current_price": None, "price_analysis": {}}
    available = {
        "current_price": 0.11,
        "highest_price": 0.2,
        "lowest_price": 0.05,
        "price_analysis": {"data_available": True},
    }

    await coordinator._check_data_availability(unavailable)
    now_ref["value"] = base_time + timedelta(seconds=10)
    await coordinator._check_data_availability(available)

    send_notification.assert_not_awaited()
    assert coordinator.notification_sent is False
    assert coordinator.data_unavailable_since is None


def _event_for(entity_id: str):
    return SimpleNamespace(data={"entity_id": entity_id})


@pytest.mark.asyncio
async def test_handle_entity_change_respects_throttle(fake_hass, monkeypatch):
    config = _base_config()
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    base_time = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    clock = {"now": base_time}
    monkeypatch.setattr(
        coordinator_module.dt_util, "utcnow", lambda: clock["now"], raising=False
    )

    coordinator.async_request_refresh = AsyncMock()
    tasks: list[asyncio.Task] = []

    original_create_task = fake_hass.async_create_task

    def capture_task(coro):
        task = original_create_task(coro)
        tasks.append(task)
        return task

    fake_hass.async_create_task = capture_task

    event = _event_for(config[CONF_CURRENT_PRICE_ENTITY])

    coordinator._handle_entity_change(event)
    for task in tasks:
        await task
    assert len(tasks) == 1
    assert coordinator.async_request_refresh.await_count == 1

    clock["now"] = base_time + timedelta(seconds=5)
    coordinator._handle_entity_change(event)
    # Task is created but throttled inside _async_handle_throttled_update
    for task in tasks[1:]:
        await task
    assert len(tasks) == 2
    # Refresh should still be 1 because the second call was throttled
    assert coordinator.async_request_refresh.await_count == 1

    clock["now"] = base_time + timedelta(seconds=15)
    coordinator._handle_entity_change(event)
    for task in tasks[2:]:
        await task
    assert len(tasks) == 3
    assert coordinator.async_request_refresh.await_count == 2


@pytest.mark.asyncio
async def test_handle_entity_change_includes_three_phase_entities(
    fake_hass, monkeypatch
):
    config = _three_phase_config()
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    base_time = datetime(2024, 2, 1, 8, 0, tzinfo=timezone.utc)
    clock = {"now": base_time}
    monkeypatch.setattr(
        coordinator_module.dt_util, "utcnow", lambda: clock["now"], raising=False
    )

    coordinator.async_request_refresh = AsyncMock()
    tasks: list[asyncio.Task] = []
    original_create_task = fake_hass.async_create_task

    def capture_task(coro):
        task = original_create_task(coro)
        tasks.append(task)
        return task

    fake_hass.async_create_task = capture_task

    phase_solar_entity = config[CONF_PHASES]["phase_1"][CONF_PHASE_SOLAR_ENTITY]
    event = _event_for(phase_solar_entity)

    coordinator._handle_entity_change(event)
    for task in tasks:
        await task
    assert len(tasks) == 1
    assert coordinator.async_request_refresh.await_count == 1

    # Throttle still applies for rapid subsequent updates
    clock["now"] = base_time + timedelta(seconds=5)
    coordinator._handle_entity_change(event)
    # Task is created but throttled inside _async_handle_throttled_update
    for task in tasks[1:]:
        await task
    assert len(tasks) == 2
    # Refresh should still be 1 because the second call was throttled
    assert coordinator.async_request_refresh.await_count == 1

    clock["now"] = base_time + timedelta(seconds=15)
    coordinator._handle_entity_change(event)
    for task in tasks[2:]:
        await task
    assert len(tasks) == 3
    assert coordinator.async_request_refresh.await_count == 2


@pytest.mark.asyncio
async def test_throttled_update_does_not_retrigger_after_slow_refresh(
    fake_hass, monkeypatch
):
    """Events that arrive inside the throttle window should stay throttled even if refresh is slow."""
    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)

    base_time = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    clock = {"now": base_time}
    monkeypatch.setattr(
        coordinator_module.dt_util, "utcnow", lambda: clock["now"], raising=False
    )

    first_refresh_started = asyncio.Event()
    release_first_refresh = asyncio.Event()
    refresh_calls = 0

    async def slow_refresh():
        nonlocal refresh_calls
        refresh_calls += 1
        if refresh_calls == 1:
            first_refresh_started.set()
            await release_first_refresh.wait()

    coordinator.async_request_refresh = AsyncMock(side_effect=slow_refresh)

    first_task = asyncio.create_task(
        coordinator._async_handle_throttled_update("sensor.current_price")
    )
    await first_refresh_started.wait()

    clock["now"] = base_time + timedelta(seconds=5)
    second_task = asyncio.create_task(
        coordinator._async_handle_throttled_update("sensor.current_price")
    )
    await asyncio.sleep(0)

    clock["now"] = base_time + timedelta(seconds=15)
    release_first_refresh.set()

    await asyncio.gather(first_task, second_task)

    assert coordinator.async_request_refresh.await_count == 1


@pytest.mark.asyncio
async def test_handle_entity_change_triggers_for_peak_and_tariff_entities(
    fake_hass, monkeypatch
):
    """Tracked non-power entities should refresh immediately on state changes."""
    config = _base_config()
    config[CONF_MONTHLY_GRID_PEAK_ENTITY] = "sensor.monthly_peak"
    config[CONF_GRID_POWER_ENTITY] = "sensor.grid_power"
    config[CONF_P1_TARIFF_ENTITY] = "sensor.p1_tariff"
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    base_time = datetime(2024, 2, 1, 8, 0, tzinfo=timezone.utc)
    clock = {"now": base_time}
    monkeypatch.setattr(
        coordinator_module.dt_util, "utcnow", lambda: clock["now"], raising=False
    )

    coordinator.async_request_refresh = AsyncMock()
    tasks: list[asyncio.Task] = []
    original_create_task = fake_hass.async_create_task

    def capture_task(coro):
        task = original_create_task(coro)
        tasks.append(task)
        return task

    fake_hass.async_create_task = capture_task

    coordinator._handle_entity_change(_event_for(config[CONF_GRID_POWER_ENTITY]))
    coordinator._handle_entity_change(_event_for(config[CONF_P1_TARIFF_ENTITY]))
    for task in tasks:
        await task

    assert len(tasks) == 2
    assert coordinator.async_request_refresh.await_count == 1


def test_get_current_price_interval_start_uses_active_timeline(fake_hass, monkeypatch):
    """Stable-threshold snapshots should follow the real Nord Pool interval start."""
    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    base_time = datetime(2025, 10, 14, 8, 35, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    coordinator._last_price_timeline = [
        (
            datetime(2025, 10, 14, 8, 0, tzinfo=timezone.utc),
            datetime(2025, 10, 14, 9, 0, tzinfo=timezone.utc),
            0.10,
        ),
        (
            datetime(2025, 10, 14, 9, 0, tzinfo=timezone.utc),
            datetime(2025, 10, 14, 10, 0, tzinfo=timezone.utc),
            0.12,
        ),
    ]

    assert coordinator._get_current_price_interval_start() == datetime(
        2025, 10, 14, 8, 0, tzinfo=timezone.utc
    )


@pytest.mark.asyncio
async def test_nordpool_fetch_prices_calls_service(fake_hass, monkeypatch):
    """Test that Nord Pool service is called correctly."""
    from custom_components.electricity_planner.const import CONF_NORDPOOL_CONFIG_ENTRY

    config = _base_config()
    config[CONF_NORDPOOL_CONFIG_ENTRY] = "test_config_entry_id"
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    # Mock the service call to return price data
    fake_hass.services.async_call = AsyncMock(
        return_value={
            "BE": [
                {
                    "start": "2025-10-14T00:00:00+00:00",
                    "end": "2025-10-14T00:15:00+00:00",
                    "price": 104.85,
                },
                {
                    "start": "2025-10-14T00:15:00+00:00",
                    "end": "2025-10-14T00:30:00+00:00",
                    "price": 97.53,
                },
            ]
        }
    )

    result = await coordinator._fetch_nordpool_prices("test_config_entry_id", "today")

    # Verify service was called
    fake_hass.services.async_call.assert_awaited_once()
    call_args = fake_hass.services.async_call.call_args
    domain, service, payload = call_args.args[:3]
    assert domain == "nordpool"
    assert service == "get_prices_for_date"
    assert payload["config_entry"] == "test_config_entry_id"
    assert call_args.kwargs["return_response"] is True

    # Verify result
    assert result is not None
    assert "BE" in result
    assert len(result["BE"]) == 2
    assert result["BE"][0]["price"] == 104.85


@pytest.mark.asyncio
async def test_nordpool_cache_prevents_redundant_calls(fake_hass, monkeypatch):
    """Test that Nord Pool prices are cached and service isn't called again."""
    from custom_components.electricity_planner.const import CONF_NORDPOOL_CONFIG_ENTRY

    config = _base_config()
    config[CONF_NORDPOOL_CONFIG_ENTRY] = "test_config_entry_id"
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    base_time = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    clock = {"now": base_time}
    monkeypatch.setattr(
        coordinator_module.dt_util, "utcnow", lambda: clock["now"], raising=False
    )
    monkeypatch.setattr(
        coordinator_module.dt_util, "now", lambda: clock["now"], raising=False
    )

    mock_response = {
        "BE": [
            {
                "start": "2025-10-14T00:00:00+00:00",
                "end": "2025-10-14T00:15:00+00:00",
                "price": 104.85,
            },
        ]
    }
    fake_hass.services.async_call = AsyncMock(return_value=mock_response)

    # First call - should hit service
    result1 = await coordinator._fetch_nordpool_prices("test_config_entry_id", "today")
    assert fake_hass.services.async_call.await_count == 1
    assert result1 == mock_response

    # Second call within 5 minutes - should use cache
    clock["now"] = base_time + timedelta(minutes=2)
    result2 = await coordinator._fetch_nordpool_prices("test_config_entry_id", "today")
    assert fake_hass.services.async_call.await_count == 1  # Still 1, not called again
    assert result2 == mock_response

    # Third call after 5 minutes - should hit service again
    clock["now"] = base_time + timedelta(minutes=6)
    result3 = await coordinator._fetch_nordpool_prices("test_config_entry_id", "today")
    assert fake_hass.services.async_call.await_count == 2  # Called again
    assert result3 == mock_response


@pytest.mark.asyncio
async def test_nordpool_cache_rolls_over_at_midnight(fake_hass, monkeypatch):
    """Crossing midnight should invalidate the cached 'today' payload immediately."""
    from custom_components.electricity_planner.const import CONF_NORDPOOL_CONFIG_ENTRY

    config = _base_config()
    config[CONF_NORDPOOL_CONFIG_ENTRY] = "test_config_entry_id"
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    clock = {"now": datetime(2024, 1, 1, 23, 58, tzinfo=timezone.utc)}
    monkeypatch.setattr(
        coordinator_module.dt_util, "utcnow", lambda: clock["now"], raising=False
    )
    monkeypatch.setattr(
        coordinator_module.dt_util, "now", lambda: clock["now"], raising=False
    )

    day_one = {"BE": [{"start": "2024-01-01T00:00:00+00:00", "price": 100.0}]}
    day_two = {"BE": [{"start": "2024-01-02T00:00:00+00:00", "price": 200.0}]}
    fake_hass.services.async_call = AsyncMock(side_effect=[day_one, day_two])

    result1 = await coordinator._fetch_nordpool_prices("test_config_entry_id", "today")
    assert result1 == day_one
    assert fake_hass.services.async_call.await_count == 1

    clock["now"] = datetime(2024, 1, 2, 0, 2, tzinfo=timezone.utc)
    result2 = await coordinator._fetch_nordpool_prices("test_config_entry_id", "today")

    assert result2 == day_two
    assert fake_hass.services.async_call.await_count == 2


@pytest.mark.asyncio
async def test_nordpool_handles_service_failure(fake_hass, monkeypatch):
    """Test that Nord Pool service failures are handled gracefully."""
    from custom_components.electricity_planner.const import CONF_NORDPOOL_CONFIG_ENTRY

    config = _base_config()
    config[CONF_NORDPOOL_CONFIG_ENTRY] = "test_config_entry_id"
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    # Mock service to raise exception
    fake_hass.services.async_call = AsyncMock(
        side_effect=Exception("Service unavailable")
    )

    result = await coordinator._fetch_nordpool_prices("test_config_entry_id", "today")

    # Should return None instead of crashing
    assert result is None
    # With retry logic, it should be called 3 times (initial + 2 retries)
    assert fake_hass.services.async_call.await_count == 3


@pytest.mark.asyncio
async def test_fetch_all_data_includes_nordpool_prices(fake_hass, monkeypatch):
    """Test that _fetch_all_data includes Nord Pool price data when configured."""
    from custom_components.electricity_planner.const import CONF_NORDPOOL_CONFIG_ENTRY

    config = _base_config()
    config[CONF_NORDPOOL_CONFIG_ENTRY] = "test_config_entry_id"
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    # Freeze time so date selection is deterministic
    base_time = datetime(2025, 10, 13, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        coordinator_module.dt_util, "now", lambda: base_time, raising=False
    )
    monkeypatch.setattr(
        coordinator_module.dt_util, "utcnow", lambda: base_time, raising=False
    )

    # Set up entity states
    fake_hass.states.set("sensor.current_price", "0.12")
    fake_hass.states.set("sensor.highest_price", "0.30")
    fake_hass.states.set("sensor.lowest_price", "0.05")
    fake_hass.states.set("sensor.next_price", "0.10")
    fake_hass.states.set("sensor.battery_soc_1", "50")
    fake_hass.states.set("sensor.battery_soc_2", "60")
    fake_hass.states.set("sensor.solar_production", "2000")
    fake_hass.states.set("sensor.house_consumption", "1500")
    fake_hass.states.set("sensor.car_power", "0")

    # Mock Nord Pool service responses
    today_prices = {
        "BE": [
            {
                "start": "2025-10-14T10:00:00+00:00",
                "end": "2025-10-14T10:15:00+00:00",
                "price": 100.0,
            }
        ]
    }
    tomorrow_prices = {
        "BE": [
            {
                "start": "2025-10-15T10:00:00+00:00",
                "end": "2025-10-15T10:15:00+00:00",
                "price": 110.0,
            }
        ]
    }
    target_today = base_time.date().isoformat()
    target_tomorrow = (base_time + timedelta(days=1)).date().isoformat()

    call_count = [0]

    async def mock_service_call(
        domain, service, data, blocking=False, return_response=False
    ):
        call_count[0] += 1
        if data["date"] == target_today:
            return today_prices
        elif data["date"] == target_tomorrow:
            return tomorrow_prices
        raise AssertionError(f"Unexpected date requested: {data['date']}")

    fake_hass.services.async_call = mock_service_call

    data = await coordinator._fetch_all_data()

    # Verify Nord Pool data is included
    assert data["nordpool_prices_today"] is not None
    assert data["nordpool_prices_tomorrow"] is not None
    assert "BE" in data["nordpool_prices_today"]
    assert "BE" in data["nordpool_prices_tomorrow"]
    assert data["nordpool_prices_today"]["BE"][0]["price"] == 100.0
    assert data["nordpool_prices_tomorrow"]["BE"][0]["price"] == 110.0


def _make_price_interval(start, value):
    return {
        "start": start.isoformat(),
        "end": (start + timedelta(minutes=15)).isoformat(),
        "value": value,
    }


def _freeze_time(monkeypatch, base_time):
    monkeypatch.setattr(
        coordinator_module.dt_util, "now", lambda: base_time, raising=False
    )
    monkeypatch.setattr(
        coordinator_module.dt_util, "utcnow", lambda: base_time, raising=False
    )


def _seed_energy_ledger(
    coordinator, battery_details, origin=EnergyOrigin.SOLAR, buy_cost=None
):
    """Give the planner a trusted ledger that fully accounts for the fleet.

    Arbitrage export fails closed on an unattributed ledger, so slot and price
    tests seed one lot of a known origin covering the whole observed inventory.
    The configured fleet is narrowed to the batteries in ``battery_details``:
    arbitrage also fails closed when a configured battery is not reporting.
    """
    coordinator.config[CONF_BATTERY_SOC_ENTITIES] = [
        battery["entity_id"] for battery in battery_details
    ]
    total = sum(
        float(battery["capacity"]) * float(battery["soc"]) / 100
        for battery in battery_details
    )
    ledger = BatteryEnergyLedger()
    ledger.add(origin, total, buy_cost)
    coordinator._energy_ledger = ledger
    coordinator._ledger_initialized = True
    coordinator._ledger_last_energy_kwh = total
    coordinator._ledger_last_observed_at = coordinator_module.dt_util.utcnow()
    coordinator._ledger_inventory = tuple(
        sorted(
            (battery["entity_id"], round(float(battery["capacity"]), 6))
            for battery in battery_details
        )
    )
    coordinator._ledger_status = "seeded"
    return coordinator


def _arbitrage_plan(coordinator, data, **seed_kwargs):
    """Seed a trusted ledger from ``data`` and build the arbitrage plan."""
    _seed_energy_ledger(coordinator, data.get("battery_details") or [], **seed_kwargs)
    return coordinator._calculate_arbitrage_mode_plan(data)


@pytest.mark.parametrize(
    "multiplier,offset,transport_lookup,expected",
    [
        (
            1.0,
            0.0,
            [
                # Week-old data: what transport cost was 7 days ago at 08:00 and 09:00
                {"start": "2025-10-07T08:00:00+00:00", "cost": 0.02},
                {"start": "2025-10-07T09:00:00+00:00", "cost": 0.03},
            ],
            0.1431,  # (0.12+0.15)/2 * 1.06 VAT
        ),
        (
            1.1,
            0.05,
            [
                # Week-old data: what transport cost was 7 days ago at 08:00 and 09:00
                {"start": "2025-10-07T08:00:00+00:00", "cost": 0.02},
                {"start": "2025-10-07T09:00:00+00:00", "cost": 0.03},
            ],
            0.2078,  # ((0.16+0.02)+(0.182+0.03))/2 * 1.06 VAT = 0.20776
        ),
    ],
)
def test_calculate_average_threshold(
    fake_hass, monkeypatch, multiplier, offset, transport_lookup, expected
):
    """Average threshold should use week-old transport costs for future prices (€/kWh)."""
    base_time = datetime(2025, 10, 14, 8, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    config.update(
        {
            CONF_TRANSPORT_COST_ENTITY: "sensor.transport_cost",
            "price_adjustment_multiplier": multiplier,
            "price_adjustment_offset": offset,
        }
    )
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    # Two future intervals (15 and 60 minutes ahead)
    prices_today = {
        "BE": [
            _make_price_interval(
                base_time + timedelta(minutes=15), 100.0
            ),  # 0.1 €/kWh base
            _make_price_interval(
                base_time + timedelta(hours=1), 120.0
            ),  # 0.12 €/kWh base
        ]
    }

    result = coordinator._calculate_average_threshold(
        prices_today, None, transport_lookup
    )
    assert result == pytest.approx(expected, rel=1e-6)


def test_calculate_average_threshold_skips_past(fake_hass, monkeypatch):
    """Intervals in the past should be ignored."""
    base_time = datetime(2025, 10, 14, 8, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    prices_today = {
        "BE": [
            _make_price_interval(base_time - timedelta(hours=1), 80.0),  # past interval
            _make_price_interval(
                base_time + timedelta(minutes=30), 100.0
            ),  # future interval
        ]
    }

    result = coordinator._calculate_average_threshold(prices_today, None, None)
    # Only the future interval remains: (100/1000) * 1.06 VAT = 0.106
    assert result == pytest.approx(0.106, rel=1e-6)


def test_calculate_average_threshold_backfills_with_past(fake_hass, monkeypatch):
    """With a single future slot, average should backfill ~24h using past data."""
    base_time = datetime(2025, 10, 14, 22, 45, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    intervals = []
    # 95 past intervals (15 min cadence) at 0.1 €/kWh base
    for steps_back in range(95, 0, -1):
        start = base_time - timedelta(minutes=15 * steps_back)
        intervals.append(_make_price_interval(start, 100.0))

    # Single future interval with higher price
    intervals.append(_make_price_interval(base_time, 200.0))

    prices_today = {"BE": intervals}

    result = coordinator._calculate_average_threshold(prices_today, None, None)
    # Average of (95*0.1+0.2)/96 * 1.06 VAT ≈ 0.1071
    assert result == pytest.approx(0.1071, rel=1e-6)


def test_calculate_average_threshold_insufficient_past_uses_future_only(
    fake_hass, monkeypatch
):
    """If there aren't enough historical slots, fall back to future-only average."""
    base_time = datetime(2025, 10, 14, 2, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    intervals = []
    # Minimal past data (not enough to backfill 24h)
    intervals.append(_make_price_interval(base_time - timedelta(minutes=15), 90.0))
    intervals.append(_make_price_interval(base_time - timedelta(minutes=30), 95.0))

    # Two future intervals that should dominate the average
    intervals.append(_make_price_interval(base_time, 100.0))
    intervals.append(_make_price_interval(base_time + timedelta(minutes=15), 130.0))

    prices_today = {"BE": intervals}

    result = coordinator._calculate_average_threshold(prices_today, None, None)
    # Future-only average: (0.100 + 0.130) / 2 * 1.06 VAT ≈ 0.1219
    assert result == pytest.approx(0.1219, rel=1e-6)


@pytest.mark.parametrize("use_average", [True, False])
def test_check_minimum_charging_window(fake_hass, monkeypatch, use_average):
    """Charging window detection honors threshold selection and interval continuity."""
    base_time = datetime(2025, 10, 14, 8, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    config.update(
        {
            CONF_USE_AVERAGE_THRESHOLD: use_average,
            "price_adjustment_multiplier": 1.0,
            "price_adjustment_offset": 0.0,
            CONF_TRANSPORT_COST_ENTITY: "sensor.transport_cost",
            CONF_PRICE_THRESHOLD: 0.07,
        }
    )
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    # Transport lookup: 0.01 €/kWh for 8-10h (from week ago for pattern matching)
    transport_lookup = [
        {"start": "2025-10-07T08:00:00+00:00", "cost": 0.01},
        {"start": "2025-10-07T09:00:00+00:00", "cost": 0.01},
        {"start": "2025-10-07T10:00:00+00:00", "cost": 0.01},
    ]

    # Eight consecutive prices = 0.065 €/kWh base + 0.01 transport = 0.075 final (2 hours total)
    intervals = [
        _make_price_interval(base_time + timedelta(minutes=15 * i), 65.0)
        for i in range(8)
    ]
    prices_today = {"BE": intervals}

    average = coordinator._calculate_average_threshold(
        prices_today, None, transport_lookup
    )
    if use_average:
        coordinator._average_threshold_enabled = True

    # Average threshold ≈ 0.075 -> qualifies when using average, fails fixed (0.07)
    result = coordinator._check_minimum_charging_window(
        prices_today,
        None,
        transport_lookup,
        None,
        average,
    )

    if use_average:
        assert result is True
    else:
        assert result is False


def test_check_minimum_charging_window_ignores_average_until_enabled(
    fake_hass, monkeypatch
):
    """Average-threshold warm-up should not affect window checks until enabled."""
    base_time = datetime(2025, 10, 14, 8, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    config.update(
        {
            CONF_USE_AVERAGE_THRESHOLD: True,
            CONF_PRICE_THRESHOLD: 0.07,
            "price_adjustment_multiplier": 1.0,
            "price_adjustment_offset": 0.0,
            CONF_TRANSPORT_COST_ENTITY: "sensor.transport_cost",
        }
    )
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    transport_lookup = [
        {"start": "2025-10-07T08:00:00+00:00", "cost": 0.01},
        {"start": "2025-10-07T09:00:00+00:00", "cost": 0.01},
    ]
    prices_today = {
        "BE": [
            _make_price_interval(base_time + timedelta(minutes=15 * i), 65.0)
            for i in range(8)
        ]
    }

    average = coordinator._calculate_average_threshold(
        prices_today, None, transport_lookup
    )

    assert coordinator._average_threshold_enabled is False
    assert average is not None
    assert (
        coordinator._check_minimum_charging_window(
            prices_today,
            None,
            transport_lookup,
            None,
            average,
        )
        is False
    )


@pytest.mark.asyncio
async def test_async_update_data_masks_average_threshold_until_enabled(
    fake_hass, monkeypatch
):
    """Decision payload should not activate average threshold during hysteresis warm-up."""
    config = _base_config()
    config.update(
        {
            CONF_USE_AVERAGE_THRESHOLD: True,
            CONF_PRICE_THRESHOLD: 0.12,
        }
    )
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    coordinator._fetch_all_data = AsyncMock(
        return_value={
            "average_threshold": 0.05,
            "battery_details": [],
            "battery_soc": [],
            "current_price": 0.10,
            "highest_price": 0.20,
            "lowest_price": 0.05,
            "next_price": 0.09,
        }
    )
    coordinator.decision_engine.evaluate_charging_decision = AsyncMock(return_value={})
    coordinator._check_data_availability = AsyncMock()

    await coordinator._async_update_data()

    decision_input = (
        coordinator.decision_engine.evaluate_charging_decision.await_args.args[0]
    )
    assert decision_input["average_threshold"] is None
    assert decision_input["average_threshold_candidate"] == pytest.approx(
        0.05, rel=1e-6
    )
    assert decision_input["average_threshold_active"] is False


def test_check_minimum_charging_window_respects_duration(fake_hass, monkeypatch):
    """Charging window requires duration >= DEFAULT_MIN_CAR_CHARGING_DURATION."""
    base_time = datetime(2025, 10, 14, 8, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    config.update(
        {
            CONF_PRICE_THRESHOLD: 0.08,
            "price_adjustment_multiplier": 1.0,
            "price_adjustment_offset": 0.0,
        }
    )
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    # Only one low-price interval (30 minutes) < required duration
    intervals = [
        _make_price_interval(base_time + timedelta(minutes=0), 60.0),
        _make_price_interval(
            base_time + timedelta(minutes=30), 120.0
        ),  # high price breaks window
    ]
    prices_today = {"BE": intervals}

    result = coordinator._check_minimum_charging_window(
        prices_today, None, None, None, None
    )
    assert result is False


def test_minimum_charging_window_uses_permissive_threshold(fake_hass, monkeypatch):
    """Permissive mode should expand the low-price window using the permissive threshold."""
    base_time = datetime(2025, 10, 14, 8, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    config.update(
        {
            CONF_PRICE_THRESHOLD: 0.17,  # Base threshold 0.17 €/kWh
            CONF_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER: 1.2,  # 20% boost
            "price_adjustment_multiplier": 1.0,
            "price_adjustment_offset": 0.0,
        }
    )
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    # Eight consecutive intervals (2h) at 0.19 €/kWh (above base threshold but within permissive)
    intervals = [
        _make_price_interval(base_time + timedelta(minutes=15 * i), 190.0)
        for i in range(8)
    ]
    prices_today = {"BE": intervals}
    multiplier = config[CONF_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER]

    base_window = coordinator._check_minimum_charging_window(
        prices_today,
        None,
        None,
        None,
        None,
        False,
        multiplier,
    )
    assert base_window is False

    permissive_window = coordinator._check_minimum_charging_window(
        prices_today,
        None,
        None,
        None,
        None,
        True,
        multiplier,
    )
    assert permissive_window is True


def test_check_minimum_charging_window_single_interval_too_short(
    fake_hass, monkeypatch
):
    """Single 15-minute low-price interval should not satisfy 2-hour requirement."""
    base_time = datetime(2025, 10, 14, 8, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    config.update(
        {
            CONF_PRICE_THRESHOLD: 0.09,
            "price_adjustment_multiplier": 1.0,
            "price_adjustment_offset": 0.0,
        }
    )
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    # Only one 15-minute low price interval
    intervals = [
        _make_price_interval(base_time + timedelta(minutes=0), 80.0),
    ]
    prices_today = {"BE": intervals}

    result = coordinator._check_minimum_charging_window(
        prices_today, None, None, None, None
    )
    assert result is False


@pytest.mark.asyncio
async def test_manual_override_application_and_expiry(fake_hass, monkeypatch):
    """Manual overrides should modify decisions until expiration."""
    base_time = datetime(2025, 6, 1, 6, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)

    await coordinator.async_set_manual_override(
        target="battery",
        value=True,
        duration=timedelta(minutes=30),
        reason="boost",
    )

    decision = {
        "battery_grid_charging": False,
        "battery_grid_charging_reason": "price too high",
        "strategy_trace": [],
    }

    overridden, changed = coordinator._apply_manual_overrides(decision)
    assert changed == {"battery_grid_charging"}
    assert overridden["battery_grid_charging"] is True
    assert "override" in overridden["battery_grid_charging_reason"]
    assert overridden["manual_overrides"]["battery_grid_charging"]["reason"] == "boost"
    assert overridden["strategy_trace"][-1]["strategy"] == "ManualOverride"

    _freeze_time(monkeypatch, base_time + timedelta(minutes=31))
    follow_up, cleared = coordinator._apply_manual_overrides(
        {
            "battery_grid_charging": False,
            "battery_grid_charging_reason": "reset",
            "strategy_trace": [],
        }
    )
    assert follow_up["battery_grid_charging"] is False
    assert cleared == set()
    # After expiration, the key should be set to None (cleared)
    assert coordinator._manual_overrides["battery_grid_charging"] is None


@pytest.mark.asyncio
async def test_manual_override_recomputes_gridpoint(fake_hass, monkeypatch):
    """Forcing battery charge should refresh grid setpoint and components."""
    base_time = datetime(2025, 6, 1, 6, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)

    decision = {
        "battery_grid_charging": False,
        "battery_grid_charging_reason": "automatic decision",
        "car_grid_charging": False,
        "car_grid_charging_reason": "automatic decision",
        "grid_setpoint": 0,
        "grid_setpoint_reason": "No grid charging needed",
        "grid_components": {"battery": 0, "car": 0},
        "charger_limit": 0,
        "charger_limit_reason": "Car idle",
        "battery_analysis": {"average_soc": 25, "max_soc_threshold": 80},
        "price_analysis": {},
        "power_allocation": {
            "solar_for_car": 0,
            "car_current_solar_usage": 0,
            "remaining_solar": 0,
        },
        "strategy_trace": [],
    }

    baseline_data = {
        "current_price": 0.1,
        "monthly_grid_peak": 0,
        "car_charging_power": 0,
        "car_grid_charging": False,
        "car_solar_only": False,
    }

    await coordinator.async_set_manual_override(
        target="battery",
        value=True,
        duration=None,
        reason="force charge",
    )

    overridden, changed = coordinator._apply_manual_overrides(decision)
    assert changed == {"battery_grid_charging"}

    updated = coordinator.decision_engine.recalculate_after_override(
        baseline_data, overridden, changed
    )

    expected_setpoint = int(
        coordinator.decision_engine._settings.base_grid_setpoint * 0.9
    )
    assert updated["grid_setpoint"] == expected_setpoint
    assert updated["grid_components"]["battery"] == expected_setpoint
    assert updated["grid_components"]["car"] == 0
    assert "battery" in updated["grid_setpoint_reason"]


@pytest.mark.asyncio
async def test_manual_car_wait_override_recomputes_stale_car_grid_usage(
    fake_hass,
    monkeypatch,
):
    """An active car wait override must keep derived grid usage clamped to zero."""
    base_time = datetime(2025, 6, 1, 6, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)

    await coordinator.async_set_manual_override(
        target="car",
        value=False,
        duration=None,
        reason="force wait",
    )

    decision = {
        "battery_grid_charging": False,
        "battery_grid_charging_reason": "automatic decision",
        "car_grid_charging": False,
        "car_grid_import_allowed": True,
        "car_solar_only": False,
        "car_grid_charging_reason": "automatic decision",
        "grid_setpoint": 3600,
        "grid_setpoint_reason": "Stale car import reservation",
        "grid_components": {"battery": 0, "car": 3600},
        "charger_limit": 3600,
        "charger_limit_reason": "Stale charger limit",
        "battery_analysis": {"average_soc": 60, "max_soc_threshold": 80},
        "price_analysis": {},
        "power_allocation": {
            "solar_for_car": 0,
            "car_current_solar_usage": 0,
            "remaining_solar": 0,
        },
        "strategy_trace": [],
    }

    baseline_data = {
        "current_price": 0.3,
        "monthly_grid_peak": 0,
        "car_charging_power": 3600,
        "car_grid_charging": False,
        "car_grid_import_allowed": True,
        "car_solar_only": False,
        "battery_grid_charging": False,
    }

    overridden, changed = coordinator._apply_manual_overrides(decision)
    assert changed == {"car_grid_charging"}

    updated = coordinator.decision_engine.recalculate_after_override(
        baseline_data, overridden, changed
    )

    assert updated["car_grid_import_allowed"] is False
    assert updated["car_solar_only"] is False
    assert updated["charger_limit"] == 0
    assert updated["grid_setpoint"] == 0
    assert updated["grid_components"]["battery"] == 0
    assert updated["grid_components"]["car"] == 0


class _MemoryOverrideStore:
    def __init__(self, initial_data: dict | None = None):
        self.data = initial_data

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = data


@pytest.mark.asyncio
async def test_grid_tracking_cap_persists_across_restart(fake_hass, monkeypatch):
    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    store = _MemoryOverrideStore()
    coordinator._battery_charging_state_store = store
    adjusted_at = datetime(2026, 8, 2, 19, 0, tzinfo=timezone.utc)
    coordinator._grid_tracking_charger_limit = 1680
    coordinator._grid_tracking_last_adjustment = adjusted_at

    await coordinator._async_persist_battery_charging_state()

    restored = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    restored._battery_charging_state_store = _MemoryOverrideStore(store.data)
    await restored._async_load_battery_charging_state()

    assert restored._grid_tracking_charger_limit == 1680
    assert restored._grid_tracking_last_adjustment == adjusted_at
    # A restored cap has not been commanded by this process, so it must not
    # be treated as awaiting physical acknowledgement.
    assert restored._grid_tracking_waiting_for_application is False
    assert restored._grid_tracking_cap_restored_unvalidated is True


@pytest.mark.asyncio
async def test_restored_grid_tracking_cap_does_not_throttle_before_fresh_average(
    fake_hass, monkeypatch
):
    """A persisted reduced cap must not clamp the charger during the blind window.

    After a restart the three-minute P1 averaging window restarts empty.  The
    cap restored from disk describes pre-restart conditions, so it must stay
    hidden from the decision engine until this process measures its own
    average, otherwise charging is throttled on stale evidence.
    """
    base_time = datetime(2026, 8, 2, 19, 0, tzinfo=timezone.utc)
    now_ref = {"value": base_time}
    monkeypatch.setattr(
        coordinator_module.dt_util, "utcnow", lambda: now_ref["value"], raising=False
    )
    config = _base_config()
    config[CONF_GRID_IMPORT_NEGATIVE_FEEDBACK] = True
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    # Simulate the post-restart restore path.
    coordinator._grid_tracking_charger_limit = 1680
    coordinator._grid_tracking_last_adjustment = base_time - timedelta(minutes=30)
    coordinator._grid_tracking_waiting_for_application = False
    coordinator._grid_tracking_cap_restored_unvalidated = True

    seen: dict[str, Any] = {}

    async def _capture(data, *args, **kwargs):
        seen.update(data)
        return {"battery_grid_charging": False, "car_grid_charging": True}

    coordinator._fetch_all_data = AsyncMock(
        return_value={
            "current_price": 0.11,
            "highest_price": 0.2,
            "lowest_price": 0.05,
            "grid_power": -2000,
            # No complete window yet after the restart.
            "grid_tracking_average_power": None,
        }
    )
    coordinator._check_data_availability = AsyncMock()
    coordinator.decision_engine.evaluate_charging_decision = AsyncMock(
        side_effect=_capture
    )

    await coordinator._async_update_data()

    # The decision engine must not see the stale cap, so charging is not
    # limited and no bogus acknowledgement is pending.
    assert seen["grid_tracking_charger_limit"] is None
    assert seen["grid_tracking_last_adjustment"] is None
    assert seen["grid_tracking_waiting_for_application"] is False


@pytest.mark.asyncio
async def test_restored_grid_tracking_cap_applies_once_average_available(
    fake_hass, monkeypatch
):
    """Once this process has a real average, normal feedback state resumes."""
    base_time = datetime(2026, 8, 2, 19, 0, tzinfo=timezone.utc)
    now_ref = {"value": base_time}
    monkeypatch.setattr(
        coordinator_module.dt_util, "utcnow", lambda: now_ref["value"], raising=False
    )
    config = _base_config()
    config[CONF_GRID_IMPORT_NEGATIVE_FEEDBACK] = True
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    coordinator._grid_tracking_charger_limit = 1680
    coordinator._grid_tracking_last_adjustment = base_time - timedelta(minutes=30)
    coordinator._grid_tracking_cap_restored_unvalidated = True
    coordinator._previous_car_charging = True

    seen: dict[str, Any] = {}

    async def _capture(data, *args, **kwargs):
        seen.update(data)
        return {"battery_grid_charging": False, "car_grid_charging": True}

    coordinator._fetch_all_data = AsyncMock(
        return_value={
            "current_price": 0.11,
            "highest_price": 0.2,
            "lowest_price": 0.05,
            "grid_power": -2000,
            "grid_tracking_average_power": -2000.0,
        }
    )
    coordinator._check_data_availability = AsyncMock()
    coordinator.decision_engine.evaluate_charging_decision = AsyncMock(
        side_effect=_capture
    )

    await coordinator._async_update_data()

    assert seen["grid_tracking_charger_limit"] == 1680
    assert coordinator._grid_tracking_cap_restored_unvalidated is False


@pytest.mark.asyncio
async def test_manual_override_persists_across_restart(fake_hass, monkeypatch):
    """Indefinite overrides should survive coordinator recreation."""
    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    store = _MemoryOverrideStore()
    coordinator._manual_override_store = store

    await coordinator.async_set_manual_override(
        target="battery",
        value=False,
        duration=None,
        reason="persisted disable",
    )

    restored = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    restored._manual_override_store = _MemoryOverrideStore(store.data)

    await restored._async_load_manual_overrides()

    override = restored.get_manual_override("battery_grid_charging")
    assert override is not None
    assert override["value"] is False
    assert override["reason"] == "persisted disable"


@pytest.mark.asyncio
async def test_arbitrage_mode_persists_across_restart(fake_hass, monkeypatch):
    """Arbitrage mode should survive coordinator recreation."""
    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    store = _MemoryOverrideStore()
    coordinator._runtime_mode_store = store

    await coordinator.async_set_arbitrage_mode(reason="persisted dump")

    restored = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    restored._runtime_mode_store = _MemoryOverrideStore(store.data)

    await restored._async_load_runtime_modes()

    state = restored.get_arbitrage_mode_state()
    assert state is not None
    assert state["value"] is True
    assert state["reason"] == "persisted dump"


@pytest.mark.asyncio
async def test_runtime_modes_migrate_from_legacy_manual_override_store(
    fake_hass, monkeypatch
):
    """Legacy runtime-mode entries should move out of the manual override store."""
    base_time = datetime(2025, 10, 14, 8, 0, tzinfo=timezone.utc)
    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    legacy_store = _MemoryOverrideStore(
        {
            "overrides": {
                "arbitrage_mode": {
                    "value": True,
                    "reason": "legacy arbitrage",
                    "set_at": base_time.isoformat(),
                    "expires_at": None,
                },
                "negative_buy_mode": {
                    "value": True,
                    "reason": "legacy negative buy",
                    "set_at": base_time.isoformat(),
                    "expires_at": None,
                },
                "car_grid_charging": {
                    "value": True,
                    "reason": "legacy car override",
                    "set_at": base_time.isoformat(),
                    "expires_at": None,
                },
            }
        }
    )
    runtime_store = _MemoryOverrideStore()
    coordinator._manual_override_store = legacy_store
    coordinator._runtime_mode_store = runtime_store

    await coordinator._async_load_runtime_modes()
    await coordinator._async_load_manual_overrides()

    assert coordinator.get_arbitrage_mode_state()["reason"] == "legacy arbitrage"
    assert coordinator.get_negative_buy_mode_state()["reason"] == "legacy negative buy"
    assert coordinator.get_manual_override("arbitrage_mode") is None
    assert coordinator.get_manual_override("negative_buy_mode") is None
    assert (
        coordinator.get_manual_override("car_grid_charging")["reason"]
        == "legacy car override"
    )
    assert "arbitrage_mode" in runtime_store.data["modes"]
    assert "negative_buy_mode" in runtime_store.data["modes"]
    assert "arbitrage_mode" not in legacy_store.data["overrides"]
    assert "negative_buy_mode" not in legacy_store.data["overrides"]


@pytest.mark.asyncio
async def test_car_permissive_mode_persists_across_restart(fake_hass, monkeypatch):
    """Car permissive mode should be restored from coordinator persistence."""
    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    store = _MemoryOverrideStore()
    coordinator._car_permissive_mode_store = store

    await coordinator.async_set_car_permissive_mode(reason="persisted permissive")

    assert coordinator._car_permissive_mode_active is True
    assert store.data["enabled"] is True

    restored = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    restored._car_permissive_mode_store = _MemoryOverrideStore(store.data)

    await restored._async_load_car_permissive_mode()

    assert restored._car_permissive_mode_active is True


def test_arbitrage_mode_plan_prefers_highest_export_slots(fake_hass, monkeypatch):
    """Arbitrage mode planner should derive its threshold from the highest-value eligible slots."""
    base_time = datetime(2025, 10, 14, 8, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    config.update(
        {
            CONF_ARBITRAGE_MODE_RESERVE_SOC: 20,
            "feedin_adjustment_multiplier": 1.0,
            "feedin_adjustment_offset": 0.0,
            "feedin_price_threshold": 0.05,
            "max_battery_power": 2000,
            "max_grid_power": 6000,
        }
    )
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)
    coordinator._arbitrage_mode_state = {
        "value": True,
        "reason": "Dump mode",
        "expires_at": None,
        "set_at": base_time,
    }

    lower_window = [
        _make_price_interval(base_time + timedelta(minutes=15 * slot), 70.0)
        for slot in range(8)
    ]
    higher_window = [
        _make_price_interval(base_time + timedelta(hours=2, minutes=15 * slot), 110.0)
        for slot in range(8)
    ]

    plan = _arbitrage_plan(
        coordinator,
        {
            "battery_details": [
                {
                    "entity_id": "sensor.battery_soc_1",
                    "soc": 60,
                    "capacity": 10.0,
                    "phases": ["phase_1"],
                }
            ],
            "nordpool_prices_today": {"BE": lower_window + higher_window},
            "nordpool_prices_tomorrow": None,
        },
    )

    assert plan["enabled"] is True
    assert plan["active"] is False
    assert plan["required_duration_hours"] == pytest.approx(2.0, rel=1e-6)
    assert plan["selected_slots_count"] == 8
    assert plan["arbitrage_price_threshold"] == pytest.approx(0.11, rel=1e-6)
    assert "top 8 eligible slots" in plan["reason"]


def test_arbitrage_mode_plan_honors_configured_export_cap(fake_hass, monkeypatch):
    """Configured export cap should further limit the automatic battery/grid cap."""
    base_time = datetime(2025, 10, 14, 8, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    config.update(
        {
            CONF_ARBITRAGE_MODE_RESERVE_SOC: 20,
            CONF_ARBITRAGE_MODE_MAX_EXPORT_POWER: 4500,
            "feedin_adjustment_multiplier": 1.0,
            "feedin_adjustment_offset": 0.0,
            "feedin_price_threshold": 0.05,
            "max_battery_power": 6000,
            "max_grid_power": 8000,
        }
    )
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)
    coordinator._arbitrage_mode_state = {
        "value": True,
        "reason": "Dump mode",
        "expires_at": None,
        "set_at": base_time,
    }

    intervals = [
        _make_price_interval(base_time + timedelta(minutes=15 * slot), 100.0)
        for slot in range(16)
    ]

    plan = _arbitrage_plan(
        coordinator,
        {
            "battery_details": [
                {
                    "entity_id": "sensor.battery_soc_1",
                    "soc": 65,
                    "capacity": 10.0,
                    "phases": ["phase_1"],
                }
            ],
            "nordpool_prices_today": {"BE": intervals},
            "nordpool_prices_tomorrow": None,
        },
    )

    assert plan["configured_export_cap_w"] == 4500


def test_arbitrage_mode_plan_derives_threshold_from_selected_slots(
    fake_hass, monkeypatch
):
    """The arbitrage threshold should be the lowest price among the selected top slots."""
    base_time = datetime(2025, 10, 14, 8, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    config.update(
        {
            CONF_ARBITRAGE_MODE_RESERVE_SOC: 20,
            "feedin_adjustment_multiplier": 1.0,
            "feedin_adjustment_offset": 0.0,
            "feedin_price_threshold": 0.05,
            "max_battery_power": 2000,
            "max_grid_power": 6000,
        }
    )
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)
    coordinator._arbitrage_mode_state = {
        "value": True,
        "reason": "Dump mode",
        "expires_at": None,
        "set_at": base_time,
    }

    # 3 kWh available above target needs 1.5h at 2kW = 6 slots.
    first_window = [
        _make_price_interval(base_time + timedelta(minutes=15 * slot), 80.0)
        for slot in range(4)
    ]
    second_window = [
        _make_price_interval(base_time + timedelta(hours=2, minutes=15 * slot), 120.0)
        for slot in range(4)
    ]

    plan = _arbitrage_plan(
        coordinator,
        {
            "battery_details": [
                {
                    "entity_id": "sensor.battery_soc_1",
                    "soc": 50,
                    "capacity": 10.0,
                    "phases": ["phase_1"],
                }
            ],
            "nordpool_prices_today": {"BE": first_window + second_window},
            "nordpool_prices_tomorrow": None,
        },
    )

    assert plan["slots_cover_full_arbitrage"] is True
    assert plan["selected_slots_count"] == 6
    assert plan["arbitrage_price_threshold"] == pytest.approx(0.08, rel=1e-6)
    assert plan["current_slot_price"] == pytest.approx(0.08, rel=1e-6)
    assert plan["active"] is True
    assert "arbitrage threshold 0.080€/kWh" in plan["reason"]


def test_arbitrage_mode_plan_stops_when_fleet_net_energy_is_at_reserve(
    fake_hass, monkeypatch
):
    """Batteries below reserve must offset those above it when computing arbitrage energy."""
    base_time = datetime(2025, 10, 14, 8, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    config.update(
        {
            CONF_ARBITRAGE_MODE_RESERVE_SOC: 40,
            "feedin_adjustment_multiplier": 1.0,
            "feedin_adjustment_offset": 0.0,
            "feedin_price_threshold": 0.05,
            "max_battery_power": 2000,
            "max_grid_power": 6000,
        }
    )
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)
    coordinator._arbitrage_mode_state = {
        "value": True,
        "reason": "Dump mode",
        "expires_at": None,
        "set_at": base_time,
    }

    slots = [
        _make_price_interval(base_time + timedelta(minutes=15 * slot), 120.0)
        for slot in range(8)
    ]

    plan = _arbitrage_plan(
        coordinator,
        {
            "battery_details": [
                {
                    "entity_id": "sensor.battery_soc_1",
                    "soc": 80,
                    "capacity": 10.0,
                    "phases": ["phase_1"],
                },
                {
                    "entity_id": "sensor.battery_soc_2",
                    "soc": 0,
                    "capacity": 10.0,
                    "phases": ["phase_1"],
                },
            ],
            "nordpool_prices_today": {"BE": slots},
            "nordpool_prices_tomorrow": None,
        },
    )

    assert plan["active"] is False
    assert plan["available_energy_kwh"] == pytest.approx(0.0, rel=1e-6)
    assert "reserve target already reached" in plan["reason"]


def test_arbitrage_mode_plan_uses_net_fleet_headroom_when_some_batteries_are_below_reserve(
    fake_hass, monkeypatch
):
    """A below-reserve battery should reduce, not erase, arbitrage energy when net headroom remains."""
    base_time = datetime(2025, 10, 14, 8, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    config.update(
        {
            CONF_ARBITRAGE_MODE_RESERVE_SOC: 40,
            "feedin_adjustment_multiplier": 1.0,
            "feedin_adjustment_offset": 0.0,
            "feedin_price_threshold": 0.05,
            "max_battery_power": 2000,
            "max_grid_power": 6000,
        }
    )
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)
    coordinator._arbitrage_mode_state = {
        "value": True,
        "reason": "Dump mode",
        "expires_at": None,
        "set_at": base_time,
    }

    slots = [
        _make_price_interval(base_time + timedelta(minutes=15 * slot), 120.0)
        for slot in range(8)
    ]

    plan = _arbitrage_plan(
        coordinator,
        {
            "battery_details": [
                {
                    "entity_id": "sensor.battery_soc_1",
                    "soc": 80,
                    "capacity": 10.0,
                    "phases": ["phase_1"],
                },
                {
                    "entity_id": "sensor.battery_soc_2",
                    "soc": 20,
                    "capacity": 10.0,
                    "phases": ["phase_1"],
                },
            ],
            "nordpool_prices_today": {"BE": slots},
            "nordpool_prices_tomorrow": None,
        },
    )

    assert plan["active"] is True
    assert plan["available_energy_kwh"] == pytest.approx(2.0, rel=1e-6)
    assert plan["required_duration_hours"] == pytest.approx(1.0, rel=1e-6)
    assert plan["export_power"] == 2000


def test_arbitrage_mode_plan_activates_during_current_selected_window(
    fake_hass, monkeypatch
):
    """Export should activate when the current price is at or above the arbitrage threshold."""
    base_time = datetime(2025, 10, 14, 8, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    config.update(
        {
            CONF_ARBITRAGE_MODE_RESERVE_SOC: 20,
            "feedin_adjustment_multiplier": 1.0,
            "feedin_adjustment_offset": 0.0,
            "feedin_price_threshold": 0.05,
            "max_battery_power": 2000,
            "max_grid_power": 6000,
        }
    )
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)
    coordinator._arbitrage_mode_state = {
        "value": True,
        "reason": "Dump mode",
        "expires_at": None,
        "set_at": base_time,
    }

    current_window = [
        _make_price_interval(base_time + timedelta(minutes=15 * slot), 120.0)
        for slot in range(2)
    ]
    later_window = [
        _make_price_interval(base_time + timedelta(hours=2, minutes=15 * slot), 110.0)
        for slot in range(4)
    ]

    plan = _arbitrage_plan(
        coordinator,
        {
            "battery_details": [
                {
                    "entity_id": "sensor.battery_soc_1",
                    "soc": 40,
                    "capacity": 10.0,
                    "phases": ["phase_1"],
                }
            ],
            "nordpool_prices_today": {"BE": current_window + later_window},
            "nordpool_prices_tomorrow": None,
        },
    )

    assert plan["active"] is True
    assert plan["export_power"] > 0
    assert plan["selected_slots_count"] == 4
    assert plan["arbitrage_price_threshold"] == pytest.approx(0.11, rel=1e-6)
    assert plan["current_slot_price"] == pytest.approx(0.12, rel=1e-6)
    assert (
        "Dumping battery to grid until 20% across the selected high-price windows"
        not in plan["reason"]
    )
    assert "Arbitrage export active at 0.120€/kWh" in plan["reason"]


def test_arbitrage_mode_plan_falls_back_when_total_eligible_duration_is_insufficient(
    fake_hass, monkeypatch
):
    """If there are too few eligible slots, the planner should still arm the best available threshold."""
    base_time = datetime(2025, 10, 14, 8, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    config.update(
        {
            CONF_ARBITRAGE_MODE_RESERVE_SOC: 20,
            "feedin_adjustment_multiplier": 1.0,
            "feedin_adjustment_offset": 0.0,
            "feedin_price_threshold": 0.05,
            "max_battery_power": 2000,
            "max_grid_power": 6000,
        }
    )
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)
    coordinator._arbitrage_mode_state = {
        "value": True,
        "reason": "Dump mode",
        "expires_at": None,
        "set_at": base_time,
    }

    # 5 kWh available above target needs 2.5h at 2kW, but only 2h total is eligible.
    first_window = [
        _make_price_interval(base_time + timedelta(minutes=15 * slot), 80.0)
        for slot in range(4)
    ]
    second_window = [
        _make_price_interval(base_time + timedelta(hours=2, minutes=15 * slot), 120.0)
        for slot in range(4)
    ]

    plan = _arbitrage_plan(
        coordinator,
        {
            "battery_details": [
                {
                    "entity_id": "sensor.battery_soc_1",
                    "soc": 70,
                    "capacity": 10.0,
                    "phases": ["phase_1"],
                }
            ],
            "nordpool_prices_today": {"BE": first_window + second_window},
            "nordpool_prices_tomorrow": None,
        },
    )

    assert plan["slots_cover_full_arbitrage"] is False
    assert plan["selected_slots_count"] == 8
    assert plan["arbitrage_price_threshold"] == pytest.approx(0.08, rel=1e-6)
    assert plan["active"] is True
    assert "using the best available slots" in plan["reason"]


def test_arbitrage_mode_plan_uses_whole_slots_without_partial_truncation(
    fake_hass, monkeypatch
):
    """The planner should select whole slots rather than truncating the last interval."""
    base_time = datetime(2025, 10, 14, 8, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    config.update(
        {
            CONF_ARBITRAGE_MODE_RESERVE_SOC: 20,
            "feedin_adjustment_multiplier": 1.0,
            "feedin_adjustment_offset": 0.0,
            "feedin_price_threshold": 0.05,
            "max_battery_power": 4500,
            "max_grid_power": 6000,
        }
    )
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)
    coordinator._arbitrage_mode_state = {
        "value": True,
        "reason": "Dump mode",
        "expires_at": None,
        "set_at": base_time,
    }

    # 5.0 kWh above target needs ~1.11h at 4.5kW, so whole-slot selection should use 5x15min = 1.25h.
    slots = [
        _make_price_interval(base_time + timedelta(minutes=15 * slot), 100.0)
        for slot in range(8)
    ]

    plan = _arbitrage_plan(
        coordinator,
        {
            "battery_details": [
                {
                    "entity_id": "sensor.battery_soc_1",
                    "soc": 70,
                    "capacity": 10.0,
                    "phases": ["phase_1"],
                }
            ],
            "nordpool_prices_today": {"BE": slots},
            "nordpool_prices_tomorrow": None,
        },
    )

    assert plan["selected_slots_count"] == 5
    assert len(plan["selected_slots"]) == 5
    parsed_end = dt_util.parse_datetime(plan["selected_slots"][-1]["end"])
    assert dt_util.as_utc(parsed_end) == base_time + timedelta(hours=1, minutes=15)


def test_arbitrage_mode_plan_targets_same_day_deadline_before_cutoff(
    fake_hass, monkeypatch
):
    """Before the cutoff, arbitrage planning should target today's configured deadline."""
    base_time = datetime(2025, 10, 14, 8, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    config.update(
        {
            CONF_ARBITRAGE_MODE_RESERVE_SOC: 20,
            CONF_ARBITRAGE_MODE_DEADLINE_HOUR: 9,
            "feedin_adjustment_multiplier": 1.0,
            "feedin_adjustment_offset": 0.0,
            "feedin_price_threshold": 0.05,
            "max_battery_power": 2000,
            "max_grid_power": 6000,
        }
    )
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)
    coordinator._arbitrage_mode_state = {
        "value": True,
        "reason": "Dump mode",
        "expires_at": None,
        "set_at": base_time,
    }

    before_deadline = [
        _make_price_interval(base_time + timedelta(minutes=15 * slot), 90.0)
        for slot in range(4)
    ]
    after_deadline = [
        _make_price_interval(base_time + timedelta(hours=1, minutes=15 * slot), 150.0)
        for slot in range(4)
    ]

    plan = _arbitrage_plan(
        coordinator,
        {
            "battery_details": [
                {
                    "entity_id": "sensor.battery_soc_1",
                    "soc": 28,
                    "capacity": 10.0,
                    "phases": ["phase_1"],
                }
            ],
            "nordpool_prices_today": {"BE": before_deadline},
            "nordpool_prices_tomorrow": {"BE": after_deadline},
        },
    )

    assert plan["arbitrage_price_threshold"] == pytest.approx(0.09, rel=1e-6)
    parsed_deadline = dt_util.parse_datetime(plan["deadline"])
    parsed_last_end = dt_util.parse_datetime(plan["selected_slots"][-1]["end"])
    assert dt_util.as_local(parsed_deadline).hour == 9
    assert dt_util.as_utc(parsed_deadline) == base_time + timedelta(hours=1)
    assert parsed_last_end <= parsed_deadline


def test_arbitrage_mode_plan_rolls_to_next_day_after_cutoff(fake_hass, monkeypatch):
    """After the cutoff, arbitrage planning should target the next day's configured deadline."""
    base_time = datetime(2025, 10, 14, 9, 30, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    config.update(
        {
            CONF_ARBITRAGE_MODE_RESERVE_SOC: 20,
            CONF_ARBITRAGE_MODE_DEADLINE_HOUR: 9,
            "feedin_adjustment_multiplier": 1.0,
            "feedin_adjustment_offset": 0.0,
            "feedin_price_threshold": 0.05,
            "max_battery_power": 2000,
            "max_grid_power": 6000,
        }
    )
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)
    coordinator._arbitrage_mode_state = {
        "value": True,
        "reason": "Dump mode",
        "expires_at": None,
        "set_at": base_time,
    }

    before_next_deadline = [
        _make_price_interval(base_time + timedelta(hours=14, minutes=15 * slot), 90.0)
        for slot in range(4)
    ]
    after_next_deadline = [
        _make_price_interval(
            base_time + timedelta(days=1, hours=1, minutes=15 * slot), 150.0
        )
        for slot in range(4)
    ]

    plan = _arbitrage_plan(
        coordinator,
        {
            "battery_details": [
                {
                    "entity_id": "sensor.battery_soc_1",
                    "soc": 28,
                    "capacity": 10.0,
                    "phases": ["phase_1"],
                }
            ],
            "nordpool_prices_today": {"BE": before_next_deadline},
            "nordpool_prices_tomorrow": {"BE": after_next_deadline},
        },
    )

    parsed_deadline = dt_util.parse_datetime(plan["deadline"])
    parsed_last_end = dt_util.parse_datetime(plan["selected_slots"][-1]["end"])
    assert dt_util.as_local(parsed_deadline).hour == 9
    assert dt_util.as_utc(parsed_deadline) == datetime(
        2025, 10, 15, 9, 0, tzinfo=timezone.utc
    )
    assert parsed_last_end <= parsed_deadline


def test_arbitrage_mode_deadline_skips_nonexistent_local_hour_on_dst_start(
    fake_hass, monkeypatch
):
    """A missing local cutoff hour should move to the first valid instant after the gap."""
    base_time = datetime(
        2026, 3, 29, 0, 30, tzinfo=timezone.utc
    )  # 01:30 local Brussels
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    config[CONF_ARBITRAGE_MODE_DEADLINE_HOUR] = 2
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    original_tz = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(pytz.timezone("Europe/Brussels"))

    try:
        deadline = coordinator._arbitrage_mode_deadline(base_time)
        deadline_local = dt_util.as_local(deadline)
    finally:
        dt_util.set_default_time_zone(original_tz)

    assert deadline == datetime(2026, 3, 29, 1, 0, tzinfo=timezone.utc)
    assert deadline_local.hour == 3
    assert deadline_local.minute == 0


def test_arbitrage_mode_deadline_uses_first_ambiguous_local_hour_on_dst_end(
    fake_hass, monkeypatch
):
    """A repeated local cutoff hour should resolve to the earliest matching instant."""
    base_time = datetime(
        2026, 10, 24, 23, 30, tzinfo=timezone.utc
    )  # 01:30 local Brussels
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    config[CONF_ARBITRAGE_MODE_DEADLINE_HOUR] = 2
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    original_tz = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(pytz.timezone("Europe/Brussels"))

    try:
        deadline = coordinator._arbitrage_mode_deadline(base_time)
        deadline_local = dt_util.as_local(deadline)
    finally:
        dt_util.set_default_time_zone(original_tz)

    assert deadline == datetime(2026, 10, 25, 0, 0, tzinfo=timezone.utc)
    assert deadline_local.hour == 2
    assert deadline_local.minute == 0
    assert deadline_local.utcoffset() == timedelta(hours=2)


def test_select_buy_slots_picks_cheapest_within_deadline(fake_hass, monkeypatch):
    """select_buy_slots should pick the cheapest slots and exclude any after the deadline."""
    base_time = datetime(2025, 10, 14, 6, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)

    # 8 slots: first 4 at -0.10€/kWh (within deadline), last 4 at -0.20€/kWh (excluded by deadline).
    timeline = []
    for slot in range(8):
        start = base_time + timedelta(minutes=15 * slot)
        end = start + timedelta(minutes=15)
        price = -0.10 if slot < 4 else -0.20
        timeline.append((start, end, price))
    deadline = base_time + timedelta(hours=1)

    selection = coordinator._select_buy_slots(
        timeline,
        base_time,
        timedelta(minutes=30),
        -0.05,
        latest_end=deadline,
    )

    assert selection is not None
    assert selection["selected_slots_count"] == 2
    assert selection["covers_full_charge"] is True
    assert selection["buy_price_threshold"] == pytest.approx(-0.10, rel=1e-6)
    for slot in selection["selected_slots"]:
        assert slot["end"] <= deadline
        assert slot["price"] == pytest.approx(-0.10, rel=1e-6)


def test_select_buy_slots_returns_none_when_no_slot_below_threshold(
    fake_hass, monkeypatch
):
    """select_buy_slots should return None when every slot is above the maximum price."""
    base_time = datetime(2025, 10, 14, 6, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)

    timeline = [
        (
            base_time + timedelta(minutes=15 * slot),
            base_time + timedelta(minutes=15 * (slot + 1)),
            0.10,
        )
        for slot in range(4)
    ]

    assert (
        coordinator._select_buy_slots(
            timeline,
            base_time,
            timedelta(minutes=30),
            -0.05,
        )
        is None
    )


def test_negative_buy_plan_disabled_when_mode_off(fake_hass, monkeypatch):
    """Without the override, the negative-buy plan should report disabled."""
    base_time = datetime(2025, 10, 14, 6, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)

    plan = coordinator._calculate_negative_buy_plan(
        {
            "battery_details": [],
            "nordpool_prices_today": None,
            "nordpool_prices_tomorrow": None,
        }
    )

    assert plan["enabled"] is False
    assert plan["active"] is False
    assert plan["solar_curtail_active"] is False
    assert plan["selected_slots"] == []
    assert "disabled" in plan["reason"].lower()


def test_negative_buy_plan_allows_import_without_battery_details(
    fake_hass, monkeypatch
):
    """Negative-buy is a grid import request and should not require battery details."""
    base_time = datetime(2025, 10, 14, 6, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    config.update(
        {
            CONF_NEGATIVE_BUY_THRESHOLD: -0.05,
            CONF_ARBITRAGE_MODE_DEADLINE_HOUR: 23,
            "max_grid_power": 6000,
        }
    )
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)
    coordinator._negative_buy_mode_state = {
        "value": True,
        "reason": "Negative buy",
        "expires_at": None,
        "set_at": base_time,
    }

    intervals = [
        _make_price_interval(base_time + timedelta(minutes=15 * slot), -100.0)
        for slot in range(2)
    ]

    plan = coordinator._calculate_negative_buy_plan(
        {
            "battery_details": [],
            "nordpool_prices_today": {"BE": intervals},
            "nordpool_prices_tomorrow": None,
        }
    )

    assert plan["enabled"] is True
    assert plan["required_energy_kwh"] == 0.0
    assert plan["required_duration_hours"] == 0.0
    assert plan["active"] is True
    assert plan["solar_curtail_active"] is True
    assert plan["import_power"] == 6000
    assert plan["slots_cover_full_charge"] is True
    assert "Negative Arbitrage Buy active" in plan["reason"]


def test_negative_buy_plan_calculates_required_energy_and_duration(
    fake_hass, monkeypatch
):
    """When armed, the planner should expose full-battery headroom but not gate by it."""
    base_time = datetime(2025, 10, 14, 6, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    config.update(
        {
            CONF_MAX_SOC_THRESHOLD: 90,
            CONF_NEGATIVE_BUY_THRESHOLD: -0.05,
            CONF_ARBITRAGE_MODE_DEADLINE_HOUR: 23,
            "max_battery_power": 4000,
            "max_grid_power": 6000,
        }
    )
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)
    coordinator._negative_buy_mode_state = {
        "value": True,
        "reason": "Negative buy",
        "expires_at": None,
        "set_at": base_time,
    }

    # 50% SOC on a 10kWh battery -> 5 kWh headroom to full.
    # Import cap follows the configured grid cap; the peak limiter is applied
    # later by the grid-setpoint calculator.
    intervals = [
        _make_price_interval(base_time + timedelta(minutes=15 * slot), -100.0)
        for slot in range(8)
    ]

    plan = coordinator._calculate_negative_buy_plan(
        {
            "battery_details": [
                {
                    "entity_id": "sensor.battery_soc_1",
                    "soc": 50,
                    "capacity": 10.0,
                    "phases": ["phase_1"],
                }
            ],
            "nordpool_prices_today": {"BE": intervals},
            "nordpool_prices_tomorrow": None,
        }
    )

    assert plan["enabled"] is True
    assert plan["required_energy_kwh"] == pytest.approx(5.0, rel=1e-6)
    assert plan["required_duration_hours"] == pytest.approx(0.83, rel=1e-6)
    assert plan["configured_import_cap_w"] == 6000
    assert plan["selected_slots_count"] == 8
    assert plan["slots_cover_full_charge"] is True
    assert plan["buy_price_threshold"] == pytest.approx(-0.106, rel=1e-6)
    assert plan["current_slot_price"] == pytest.approx(-0.106, rel=1e-6)
    assert plan["active"] is True
    assert plan["solar_curtail_active"] is True
    assert plan["import_power"] == 6000
    assert "Negative Arbitrage Buy active" in plan["reason"]


def test_negative_buy_plan_buys_when_battery_above_soc_ceiling(fake_hass, monkeypatch):
    """Negative prices should trigger buying even above the normal SOC ceiling."""
    base_time = datetime(2025, 10, 14, 6, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    config.update(
        {
            CONF_MAX_SOC_THRESHOLD: 90,
            CONF_NEGATIVE_BUY_THRESHOLD: -0.05,
            CONF_ARBITRAGE_MODE_DEADLINE_HOUR: 23,
            "max_grid_power": 6000,
        }
    )
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)
    coordinator._negative_buy_mode_state = {
        "value": True,
        "reason": "Negative buy",
        "expires_at": None,
        "set_at": base_time,
    }

    intervals = [
        _make_price_interval(base_time + timedelta(minutes=15 * slot), -100.0)
        for slot in range(4)
    ]

    plan = coordinator._calculate_negative_buy_plan(
        {
            "battery_details": [
                {
                    "entity_id": "sensor.battery_soc_1",
                    "soc": 95,
                    "capacity": 10.0,
                    "phases": ["phase_1"],
                }
            ],
            "nordpool_prices_today": {"BE": intervals},
            "nordpool_prices_tomorrow": None,
        }
    )

    assert plan["enabled"] is True
    assert plan["required_energy_kwh"] == pytest.approx(0.5, rel=1e-6)
    assert plan["active"] is True
    assert plan["solar_curtail_active"] is True
    assert plan["current_slot_price"] == pytest.approx(-0.106, rel=1e-6)
    assert plan["import_power"] == 6000
    assert "SOC ceiling" not in plan["reason"]


def test_negative_buy_plan_rolls_to_next_day_after_cutoff(fake_hass, monkeypatch):
    """After today's cutoff, the buy deadline should target tomorrow's configured hour."""
    base_time = datetime(2025, 10, 14, 11, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    config.update(
        {
            CONF_MAX_SOC_THRESHOLD: 90,
            CONF_NEGATIVE_BUY_THRESHOLD: -0.05,
            CONF_ARBITRAGE_MODE_DEADLINE_HOUR: 10,
            "max_battery_power": 2000,
            "max_grid_power": 6000,
        }
    )
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)
    coordinator._negative_buy_mode_state = {
        "value": True,
        "reason": "Negative buy",
        "expires_at": None,
        "set_at": base_time,
    }

    tomorrow_start = base_time + timedelta(hours=18)  # 05:00 UTC the next day
    tomorrow_intervals = [
        _make_price_interval(tomorrow_start + timedelta(minutes=15 * slot), -100.0)
        for slot in range(8)
    ]

    plan = coordinator._calculate_negative_buy_plan(
        {
            "battery_details": [
                {
                    "entity_id": "sensor.battery_soc_1",
                    "soc": 50,
                    "capacity": 10.0,
                    "phases": ["phase_1"],
                }
            ],
            "nordpool_prices_today": None,
            "nordpool_prices_tomorrow": {"BE": tomorrow_intervals},
        }
    )

    parsed_deadline = dt_util.parse_datetime(plan["deadline"])
    assert dt_util.as_utc(parsed_deadline) == datetime(
        2025, 10, 15, 10, 0, tzinfo=timezone.utc
    )
    for slot in plan["selected_slots"]:
        parsed_end = dt_util.parse_datetime(slot["end"])
        assert parsed_end <= parsed_deadline


def test_forecast_summary_uses_price_timeline(fake_hass, monkeypatch):
    """Forecast summary exposes cheapest interval and best window."""
    base_time = datetime(2025, 10, 14, 8, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    config.update(
        {
            "price_adjustment_multiplier": 1.0,
            "price_adjustment_offset": 0.0,
        }
    )
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    intervals = []
    for slot in range(16):
        start = base_time + timedelta(minutes=15 * slot)
        value = 60.0 if slot < 8 else 40.0
        intervals.append(_make_price_interval(start, value))

    prices_today = {"BE": intervals}

    average = coordinator._calculate_average_threshold(prices_today, None, None)
    coordinator._check_minimum_charging_window(prices_today, None, None, None, average)

    summary = coordinator._calculate_forecast_summary(
        prices_today,
        None,
        None,
        None,
        average,
    )

    assert summary["available"] is True
    assert summary["cheapest_interval_price"] == pytest.approx(0.0424, rel=1e-6)
    parsed_cheapest_start = dt_util.parse_datetime(summary["cheapest_interval_start"])
    assert dt_util.as_utc(parsed_cheapest_start) == base_time + timedelta(hours=2)
    assert summary["best_window_average_price"] == pytest.approx(0.0424, rel=1e-6)
    parsed_best_start = dt_util.parse_datetime(summary["best_window_start"])
    assert dt_util.as_utc(parsed_best_start) == base_time + timedelta(hours=2)


def test_forecast_summary_handles_negative_prices(fake_hass, monkeypatch):
    """Negative or free prices should still surface a best window."""
    base_time = datetime(2025, 11, 5, 10, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    config.update(
        {
            "price_adjustment_multiplier": 1.0,
            "price_adjustment_offset": 0.0,
        }
    )
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    intervals = []
    for slot in range(12):
        start = base_time + timedelta(minutes=15 * slot)
        value = 0.0 if slot < 4 else -5.0  # €/MWh, converts to 0.0 or -0.005 €/kWh
        intervals.append(_make_price_interval(start, value))

    prices_today = {"BE": intervals}

    average = coordinator._calculate_average_threshold(prices_today, None, None)
    coordinator._check_minimum_charging_window(
        prices_today,
        None,
        None,
        None,
        average,
    )

    summary = coordinator._calculate_forecast_summary(
        prices_today,
        None,
        None,
        None,
        average,
    )

    expected_start = base_time + timedelta(minutes=60)

    assert summary["available"] is True
    assert summary["cheapest_interval_price"] == pytest.approx(-0.0053, rel=1e-6)
    assert summary["best_window_average_price"] == pytest.approx(-0.0053, rel=1e-6)
    assert summary["best_window_average_price"] <= 0
    parsed_best_start = dt_util.parse_datetime(summary["best_window_start"])
    assert dt_util.as_utc(parsed_best_start) == expected_start


def test_missing_price_data_marks_forecast_stale(fake_hass, monkeypatch):
    """When price data disappears temporarily, reuse cached forecast but mark it stale."""
    base_time = datetime(2025, 11, 6, 7, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    # Populate the price timeline with current data
    intervals = [
        _make_price_interval(base_time + timedelta(minutes=15 * slot), 50.0 + slot)
        for slot in range(8)
    ]
    prices_today = {"BE": intervals}

    average = coordinator._calculate_average_threshold(prices_today, None, None)
    coordinator._check_minimum_charging_window(
        prices_today,
        None,
        None,
        None,
        average,
    )
    assert coordinator._last_price_timeline is not None

    # Price data disappears — decisions fall back but timeline is still cached
    window_result = coordinator._check_minimum_charging_window(
        None,
        None,
        None,
        None,
        None,
    )
    assert window_result is False
    assert coordinator._last_price_timeline is not None

    summary = coordinator._calculate_forecast_summary(
        None,
        None,
        None,
        None,
        None,
    )
    assert summary["available"] is True
    assert summary.get("stale") is True
    assert summary["cheapest_interval_price"] == pytest.approx(0.053, rel=1e-6)
    assert "timeline_generated_at" in summary


class FakeServiceCall:
    def __init__(self, data):
        self.data = data


@pytest.mark.asyncio
async def test_service_defaults_to_single_entry(fake_hass, monkeypatch):
    """Service should infer entry_id when only one coordinator is registered."""
    config = _base_config()
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)
    coordinator.async_set_manual_override = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()

    from custom_components.electricity_planner.__init__ import _register_services_once

    fake_hass.data.setdefault(DOMAIN, {})[coordinator.entry.entry_id] = coordinator

    _register_services_once(fake_hass)

    handler = fake_hass.services.registered[(DOMAIN, SERVICE_SET_MANUAL_OVERRIDE)][
        "handler"
    ]

    call = FakeServiceCall(
        {
            ATTR_TARGET: MANUAL_OVERRIDE_TARGET_BATTERY,
            ATTR_ACTION: MANUAL_OVERRIDE_ACTION_FORCE_CHARGE,
        }
    )

    await handler(call)

    coordinator.async_set_manual_override.assert_awaited_once()
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_service_requires_entry_when_multiple(fake_hass, monkeypatch):
    """Service without entry_id should fail when multiple coordinators exist."""
    config = _base_config()
    coord_a = _create_coordinator(fake_hass, config, monkeypatch)
    coord_b = _create_coordinator(fake_hass, config, monkeypatch)

    from custom_components.electricity_planner.__init__ import _register_services_once

    fake_hass.data.setdefault(DOMAIN, {})[coord_a.entry.entry_id] = coord_a
    fake_hass.data[DOMAIN][coord_b.entry.entry_id] = coord_b

    _register_services_once(fake_hass)

    handler = fake_hass.services.registered[(DOMAIN, SERVICE_SET_MANUAL_OVERRIDE)][
        "handler"
    ]

    with pytest.raises(HomeAssistantError):
        await handler(
            FakeServiceCall(
                {
                    ATTR_TARGET: MANUAL_OVERRIDE_TARGET_BATTERY,
                    ATTR_ACTION: MANUAL_OVERRIDE_ACTION_FORCE_CHARGE,
                }
            )
        )


@pytest.mark.asyncio
async def test_service_accepts_explicit_entry(fake_hass, monkeypatch):
    """Explicit entry_id should select the matching coordinator."""
    config = _base_config()
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)
    coordinator.async_clear_manual_override = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()

    from custom_components.electricity_planner.__init__ import _register_services_once

    fake_hass.data.setdefault(DOMAIN, {})[coordinator.entry.entry_id] = coordinator

    _register_services_once(fake_hass)

    handler = fake_hass.services.registered[(DOMAIN, SERVICE_CLEAR_MANUAL_OVERRIDE)][
        "handler"
    ]

    await handler(
        FakeServiceCall(
            {
                ATTR_ENTRY_ID: coordinator.entry.entry_id,
                ATTR_TARGET: MANUAL_OVERRIDE_TARGET_CAR,
            }
        )
    )

    coordinator.async_clear_manual_override.assert_awaited_once_with(
        MANUAL_OVERRIDE_TARGET_CAR
    )
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_transport_cost_lookup_uses_local_hour(fake_hass, monkeypatch):
    """Recorded transport costs should map to local hours, not UTC."""
    base_time = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    config[CONF_TRANSPORT_COST_ENTITY] = "sensor.transport_cost"
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    original_tz = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(pytz.timezone("Europe/Rome"))

    try:
        midnight_utc = datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)
        sample_state = SimpleNamespace(state="0.05", last_changed=midnight_utc)

        def fake_history(hass, start_time, end_time, entities):
            assert entities == ["sensor.transport_cost"]
            return {"sensor.transport_cost": [sample_state]}

        fake_recorder = ModuleType("homeassistant.components.recorder")
        fake_history_module = ModuleType("homeassistant.components.recorder.history")
        fake_history_module.get_significant_states = fake_history
        fake_recorder.history = fake_history_module

        monkeypatch.setitem(
            sys.modules, "homeassistant.components.recorder", fake_recorder
        )
        monkeypatch.setitem(
            sys.modules,
            "homeassistant.components.recorder.history",
            fake_history_module,
        )

        lookup, status = await coordinator._get_transport_cost_lookup(0.05)

        assert status == "applied"
        assert len(lookup) == 1
        change = lookup[0]
        assert change["cost"] == pytest.approx(0.05, rel=1e-6)
        change_start = dt_util.as_local(dt_util.parse_datetime(change["start"]))
        assert change_start.hour == 1
    finally:
        dt_util.set_default_time_zone(original_tz)


@pytest.mark.asyncio
async def test_transport_cost_lookup_refreshes_fallback_when_current_cost_changes(
    fake_hass,
    monkeypatch,
):
    """Fallback transport lookups should not stay stale for the full cache TTL."""
    base_time = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    config[CONF_TRANSPORT_COST_ENTITY] = "sensor.transport_cost"
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    def fake_history(hass, start_time, end_time, entities):
        assert entities == ["sensor.transport_cost"]
        return {}

    fake_recorder = ModuleType("homeassistant.components.recorder")
    fake_history_module = ModuleType("homeassistant.components.recorder.history")
    fake_history_module.get_significant_states = fake_history
    fake_recorder.history = fake_history_module

    monkeypatch.setitem(sys.modules, "homeassistant.components.recorder", fake_recorder)
    monkeypatch.setitem(
        sys.modules,
        "homeassistant.components.recorder.history",
        fake_history_module,
    )

    lookup, status = await coordinator._get_transport_cost_lookup(0.05)
    assert status == "fallback_current"
    assert lookup[0]["cost"] == pytest.approx(0.05)

    lookup, status = await coordinator._get_transport_cost_lookup(0.07)
    assert status == "fallback_current"
    assert lookup[0]["cost"] == pytest.approx(0.07)


def test_resolve_transport_cost_matches_local_week_across_dst(fake_hass, monkeypatch):
    """Weekly transport matching should reuse the same local tariff slot across DST."""
    base_time = datetime(2026, 4, 4, 12, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)

    original_tz = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(pytz.timezone("Europe/Brussels"))

    try:
        transport_lookup = [
            {"start": "2026-03-29T00:00:00+00:00", "cost": 0.02},
            {"start": "2026-03-29T01:00:00+00:00", "cost": 0.07},
        ]

        result = coordinator._resolve_transport_cost(
            transport_lookup,
            datetime(2026, 4, 5, 0, 30, tzinfo=timezone.utc),
            reference_now=base_time,
        )

        assert result == pytest.approx(0.07, rel=1e-6)
    finally:
        dt_util.set_default_time_zone(original_tz)


def test_resolve_builtin_transport_cost_uses_schedule_for_future_boundary(
    fake_hass, monkeypatch
):
    """A near-future boundary interval must not inherit the current P1 tariff code."""
    reference_now = datetime(
        2026, 4, 6, 19, 55, tzinfo=timezone.utc
    )  # 21:55 local Brussels
    _freeze_time(monkeypatch, reference_now)

    config = _base_config()
    config.update(
        {
            CONF_P1_TARIFF_ENTITY: "sensor.p1_tariff",
            CONF_TRANSPORT_COST_DAY: DEFAULT_TRANSPORT_COST_DAY,
            CONF_TRANSPORT_COST_NIGHT: DEFAULT_TRANSPORT_COST_NIGHT,
        }
    )
    fake_hass.states.set("sensor.p1_tariff", "1")
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    original_tz = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(pytz.timezone("Europe/Brussels"))

    try:
        result = coordinator._resolve_builtin_transport_cost(
            datetime(
                2026, 4, 6, 20, 0, tzinfo=timezone.utc
            ),  # 22:00 local -> night tariff
            reference_now=reference_now,
        )
    finally:
        dt_util.set_default_time_zone(original_tz)

    expected = (
        DEFAULT_TRANSPORT_COST_NIGHT
        + DEFAULT_ENERGY_TAX_ACCIJNS
        + DEFAULT_ENERGY_TAX_BIJDRAGE
        + DEFAULT_ENERGY_COST_GSC
        + DEFAULT_ENERGY_COST_WKK
    )
    assert result == pytest.approx(expected, rel=1e-6)


@pytest.mark.asyncio
async def test_fetch_all_data_populates_builtin_transport_cost(fake_hass, monkeypatch):
    """Built-in transport mode should publish the current transport cost for pricing."""
    reference_now = datetime(2026, 4, 6, 10, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, reference_now)

    config = _base_config()
    config.update(
        {
            CONF_P1_TARIFF_ENTITY: "sensor.p1_tariff",
            CONF_TRANSPORT_COST_DAY: DEFAULT_TRANSPORT_COST_DAY,
            CONF_TRANSPORT_COST_NIGHT: DEFAULT_TRANSPORT_COST_NIGHT,
        }
    )
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    fake_hass.states.set("sensor.current_price", "0.10")
    fake_hass.states.set("sensor.highest_price", "0.20")
    fake_hass.states.set("sensor.lowest_price", "0.05")
    fake_hass.states.set("sensor.next_price", "0.12")
    fake_hass.states.set("sensor.battery_soc_1", "50")
    fake_hass.states.set("sensor.battery_soc_2", "60")
    fake_hass.states.set("sensor.solar_production", "1500")
    fake_hass.states.set("sensor.house_consumption", "1000")
    fake_hass.states.set("sensor.car_power", "0")
    fake_hass.states.set("sensor.p1_tariff", "1")

    data = await coordinator._fetch_all_data()

    expected = (
        DEFAULT_TRANSPORT_COST_DAY
        + DEFAULT_ENERGY_TAX_ACCIJNS
        + DEFAULT_ENERGY_TAX_BIJDRAGE
        + DEFAULT_ENERGY_COST_GSC
        + DEFAULT_ENERGY_COST_WKK
    )
    assert data["transport_cost_status"] == "builtin"
    assert data["p1_tariff_code"] == "1"
    assert data["transport_cost"] == pytest.approx(expected, rel=1e-6)


def test_build_price_analysis_overrides_uses_interval_specific_transport(
    fake_hass, monkeypatch
):
    """Current and next price should use their own tariff slot transport costs."""
    reference_now = datetime(2026, 4, 6, 19, 55, tzinfo=timezone.utc)  # 21:55 Brussels
    _freeze_time(monkeypatch, reference_now)

    config = _base_config()
    config.update(
        {
            CONF_P1_TARIFF_ENTITY: "sensor.p1_tariff",
            CONF_TRANSPORT_COST_DAY: DEFAULT_TRANSPORT_COST_DAY,
            CONF_TRANSPORT_COST_NIGHT: DEFAULT_TRANSPORT_COST_NIGHT,
        }
    )
    fake_hass.states.set("sensor.p1_tariff", "1")
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    prices_today = {
        "BE": [
            {
                "start": "2026-04-06T19:45:00+00:00",
                "end": "2026-04-06T20:00:00+00:00",
                "value": 100.0,
            },
            {
                "start": "2026-04-06T20:00:00+00:00",
                "end": "2026-04-06T20:15:00+00:00",
                "value": 100.0,
            },
            {
                "start": "2026-04-06T21:00:00+00:00",
                "end": "2026-04-06T21:15:00+00:00",
                "value": 150.0,
            },
            {
                "start": "2026-04-06T18:00:00+00:00",
                "end": "2026-04-06T18:15:00+00:00",
                "value": 50.0,
            },
        ]
    }

    original_tz = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(pytz.timezone("Europe/Brussels"))
    try:
        overrides = coordinator._build_price_analysis_overrides(
            prices_today,
            None,
            [],
            None,
            now=reference_now,
        )
    finally:
        dt_util.set_default_time_zone(original_tz)

    day_transport = (
        DEFAULT_TRANSPORT_COST_DAY
        + DEFAULT_ENERGY_TAX_ACCIJNS
        + DEFAULT_ENERGY_TAX_BIJDRAGE
        + DEFAULT_ENERGY_COST_GSC
        + DEFAULT_ENERGY_COST_WKK
    )
    assert overrides is not None
    assert overrides["current_price"] == pytest.approx(0.24154326, rel=1e-6)
    assert overrides["next_price"] == pytest.approx(0.23083726, rel=1e-6)
    assert overrides["highest_price"] == pytest.approx(0.28383726, rel=1e-6)
    assert overrides["lowest_price"] == pytest.approx(0.18854326, rel=1e-6)
    assert overrides["transport_cost"] == pytest.approx(day_transport, rel=1e-6)


# ---------------------------------------------------------------------------
# Solar Forecast Caching (_resolve_solar_forecast) Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_solar_forecast_after_start_hour_caches_value(fake_hass, monkeypatch):
    """After start hour, the forecast entity value should be cached."""
    config = _base_config()
    config[CONF_SOLAR_FORECAST_ENTITY_TOMORROW] = "sensor.energy_production_tomorrow"
    config[CONF_SOLAR_FORECAST_START_HOUR] = 20
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    # Set time to 21:00
    tz = pytz.timezone("Europe/Brussels")
    fake_now = datetime(2025, 6, 15, 21, 0, tzinfo=tz)
    monkeypatch.setattr(dt_util, "now", lambda: fake_now)

    fake_hass.states.set("sensor.energy_production_tomorrow", "15.5")

    result = await coordinator._resolve_solar_forecast(
        "sensor.energy_production_tomorrow"
    )
    assert result == pytest.approx(15.5)
    assert coordinator._cached_solar_forecast == pytest.approx(15.5)
    assert coordinator._solar_forecast_cache_date == fake_now.date()
    assert coordinator._solar_forecast_cache_hour == 21
    assert coordinator._solar_forecast_source == "tomorrow_live"


@pytest.mark.asyncio
async def test_solar_forecast_cache_refreshes_hourly(fake_hass, monkeypatch):
    """Cache should refresh when the hour changes."""
    config = _base_config()
    config[CONF_SOLAR_FORECAST_ENTITY_TOMORROW] = "sensor.energy_production_tomorrow"
    config[CONF_SOLAR_FORECAST_START_HOUR] = 20
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    tz = pytz.timezone("Europe/Brussels")

    # First call at 20:00 → caches 10.0
    fake_hass.states.set("sensor.energy_production_tomorrow", "10.0")
    monkeypatch.setattr(dt_util, "now", lambda: datetime(2025, 6, 15, 20, 0, tzinfo=tz))
    result1 = await coordinator._resolve_solar_forecast(
        "sensor.energy_production_tomorrow"
    )
    assert result1 == pytest.approx(10.0)

    # Same hour, entity updated → still returns cached value
    fake_hass.states.set("sensor.energy_production_tomorrow", "12.0")
    monkeypatch.setattr(
        dt_util, "now", lambda: datetime(2025, 6, 15, 20, 30, tzinfo=tz)
    )
    result2 = await coordinator._resolve_solar_forecast(
        "sensor.energy_production_tomorrow"
    )
    assert result2 == pytest.approx(10.0)

    # Next hour → cache refreshes
    monkeypatch.setattr(dt_util, "now", lambda: datetime(2025, 6, 15, 21, 0, tzinfo=tz))
    result3 = await coordinator._resolve_solar_forecast(
        "sensor.energy_production_tomorrow"
    )
    assert result3 == pytest.approx(12.0)
    assert coordinator._solar_forecast_cache_hour == 21


@pytest.mark.asyncio
async def test_solar_forecast_before_start_hour_uses_today_entity(
    fake_hass, monkeypatch
):
    """Before start hour with 'today' entity configured, should use today's value."""
    config = _base_config()
    config[CONF_SOLAR_FORECAST_ENTITY_TOMORROW] = "sensor.energy_production_tomorrow"
    config[CONF_SOLAR_FORECAST_TODAY_ENTITY] = "sensor.energy_production_today"
    config[CONF_SOLAR_FORECAST_START_HOUR] = 20
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    tz = pytz.timezone("Europe/Brussels")
    monkeypatch.setattr(dt_util, "now", lambda: datetime(2025, 6, 16, 3, 0, tzinfo=tz))

    fake_hass.states.set("sensor.energy_production_tomorrow", "8.0")  # wrong day!
    fake_hass.states.set("sensor.energy_production_today", "15.0")  # correct day

    result = await coordinator._resolve_solar_forecast(
        "sensor.energy_production_tomorrow"
    )
    assert result == pytest.approx(15.0)
    assert coordinator._solar_forecast_source == "today_live"


@pytest.mark.asyncio
async def test_get_state_value_parses_localized_or_unit_appended_numbers(
    fake_hass, monkeypatch
):
    """Numeric parsing should tolerate decimal comma and unit suffixes."""
    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)

    fake_hass.states.set("sensor.localized_value", "13,239")
    fake_hass.states.set("sensor.unit_appended_value", "11.7 kWh")

    localized = await coordinator._get_state_value("sensor.localized_value")
    with_unit = await coordinator._get_state_value("sensor.unit_appended_value")

    assert localized == pytest.approx(13.239)
    assert with_unit == pytest.approx(11.7)


@pytest.mark.asyncio
async def test_solar_forecast_before_start_hour_uses_cache_when_no_today(
    fake_hass, monkeypatch
):
    """Before start hour without today entity, should use cached value from previous evening."""
    config = _base_config()
    config[CONF_SOLAR_FORECAST_ENTITY_TOMORROW] = "sensor.energy_production_tomorrow"
    config[CONF_SOLAR_FORECAST_START_HOUR] = 20
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    tz = pytz.timezone("Europe/Brussels")

    # Populate cache at 21:00 on June 15
    fake_hass.states.set("sensor.energy_production_tomorrow", "14.0")
    monkeypatch.setattr(dt_util, "now", lambda: datetime(2025, 6, 15, 21, 0, tzinfo=tz))
    await coordinator._resolve_solar_forecast("sensor.energy_production_tomorrow")
    assert coordinator._cached_solar_forecast == pytest.approx(14.0)

    # Now it's 3 AM on June 16 — before start hour, no today entity
    fake_hass.states.set("sensor.energy_production_tomorrow", "9.0")  # wrong day value
    monkeypatch.setattr(dt_util, "now", lambda: datetime(2025, 6, 16, 3, 0, tzinfo=tz))
    result = await coordinator._resolve_solar_forecast(
        "sensor.energy_production_tomorrow"
    )
    assert result == pytest.approx(14.0)  # uses cached, not live
    assert coordinator._solar_forecast_source == "overnight_cache"


@pytest.mark.asyncio
async def test_solar_forecast_before_start_hour_no_cache_no_today_returns_none(
    fake_hass, monkeypatch
):
    """Before start hour with no cache and no today entity should return None."""
    config = _base_config()
    config[CONF_SOLAR_FORECAST_ENTITY_TOMORROW] = "sensor.energy_production_tomorrow"
    config[CONF_SOLAR_FORECAST_START_HOUR] = 20
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    tz = pytz.timezone("Europe/Brussels")
    monkeypatch.setattr(dt_util, "now", lambda: datetime(2025, 6, 16, 3, 0, tzinfo=tz))

    fake_hass.states.set("sensor.energy_production_tomorrow", "9.0")  # wrong day

    result = await coordinator._resolve_solar_forecast(
        "sensor.energy_production_tomorrow"
    )
    assert result is None


@pytest.mark.asyncio
async def test_solar_forecast_before_start_hour_today_unavailable_falls_to_cache(
    fake_hass, monkeypatch
):
    """Today entity configured but unavailable → fall back to cache."""
    config = _base_config()
    config[CONF_SOLAR_FORECAST_ENTITY_TOMORROW] = "sensor.energy_production_tomorrow"
    config[CONF_SOLAR_FORECAST_TODAY_ENTITY] = "sensor.energy_production_today"
    config[CONF_SOLAR_FORECAST_START_HOUR] = 20
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    tz = pytz.timezone("Europe/Brussels")

    # Populate cache evening before
    fake_hass.states.set("sensor.energy_production_tomorrow", "13.0")
    monkeypatch.setattr(dt_util, "now", lambda: datetime(2025, 6, 15, 22, 0, tzinfo=tz))
    await coordinator._resolve_solar_forecast("sensor.energy_production_tomorrow")

    # 3 AM - today entity not set (unavailable)
    monkeypatch.setattr(dt_util, "now", lambda: datetime(2025, 6, 16, 3, 0, tzinfo=tz))
    result = await coordinator._resolve_solar_forecast(
        "sensor.energy_production_tomorrow"
    )
    assert result == pytest.approx(13.0)  # cached value


@pytest.mark.asyncio
async def test_solar_forecast_entity_unavailable_after_start_hour(
    fake_hass, monkeypatch
):
    """After start hour with entity unavailable and no cache → returns None."""
    config = _base_config()
    config[CONF_SOLAR_FORECAST_ENTITY_TOMORROW] = "sensor.energy_production_tomorrow"
    config[CONF_SOLAR_FORECAST_START_HOUR] = 20
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    tz = pytz.timezone("Europe/Brussels")
    monkeypatch.setattr(dt_util, "now", lambda: datetime(2025, 6, 15, 21, 0, tzinfo=tz))

    # Entity not set → _get_state_value returns None
    result = await coordinator._resolve_solar_forecast(
        "sensor.energy_production_tomorrow"
    )
    assert result is None


@pytest.mark.asyncio
async def test_solar_forecast_after_start_hour_ignores_previous_day_cache_if_unavailable(
    fake_hass, monkeypatch
):
    """After start hour, don't reuse previous-day cache when live forecast is unavailable."""
    config = _base_config()
    config[CONF_SOLAR_FORECAST_ENTITY_TOMORROW] = "sensor.energy_production_tomorrow"
    config[CONF_SOLAR_FORECAST_START_HOUR] = 20
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    tz = pytz.timezone("Europe/Brussels")

    # Populate previous-day cache
    fake_hass.states.set("sensor.energy_production_tomorrow", "16.0")
    monkeypatch.setattr(dt_util, "now", lambda: datetime(2025, 6, 15, 22, 0, tzinfo=tz))
    cached = await coordinator._resolve_solar_forecast(
        "sensor.energy_production_tomorrow"
    )
    assert cached == pytest.approx(16.0)
    assert (
        coordinator._solar_forecast_cache_date
        == datetime(2025, 6, 15, tzinfo=tz).date()
    )

    # New day after start hour: live forecast unavailable -> stale cache must be ignored
    fake_hass.states.set("sensor.energy_production_tomorrow", "unknown")
    monkeypatch.setattr(dt_util, "now", lambda: datetime(2025, 6, 16, 20, 0, tzinfo=tz))
    stale_result = await coordinator._resolve_solar_forecast(
        "sensor.energy_production_tomorrow"
    )
    assert stale_result is None
    assert coordinator._cached_solar_forecast is None
    assert (
        coordinator._solar_forecast_cache_date
        == datetime(2025, 6, 16, tzinfo=tz).date()
    )
    assert coordinator._solar_forecast_cache_hour == 20

    # Once live data returns, cache should refresh normally
    fake_hass.states.set("sensor.energy_production_tomorrow", "18.0")
    monkeypatch.setattr(dt_util, "now", lambda: datetime(2025, 6, 16, 21, 0, tzinfo=tz))
    refreshed = await coordinator._resolve_solar_forecast(
        "sensor.energy_production_tomorrow"
    )
    assert refreshed == pytest.approx(18.0)


@pytest.mark.asyncio
async def test_solar_forecast_cache_persists_through_midnight(fake_hass, monkeypatch):
    """Cache populated at 22:00 should persist past midnight to next day."""
    config = _base_config()
    config[CONF_SOLAR_FORECAST_ENTITY_TOMORROW] = "sensor.energy_production_tomorrow"
    config[CONF_SOLAR_FORECAST_START_HOUR] = 20
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    tz = pytz.timezone("Europe/Brussels")

    # Cache at 22:00
    fake_hass.states.set("sensor.energy_production_tomorrow", "16.0")
    monkeypatch.setattr(dt_util, "now", lambda: datetime(2025, 6, 15, 22, 0, tzinfo=tz))
    await coordinator._resolve_solar_forecast("sensor.energy_production_tomorrow")

    # 00:30 next day - no today entity
    monkeypatch.setattr(dt_util, "now", lambda: datetime(2025, 6, 16, 0, 30, tzinfo=tz))
    result = await coordinator._resolve_solar_forecast(
        "sensor.energy_production_tomorrow"
    )
    assert result == pytest.approx(16.0)

    # 19:59 next day — still before start hour, still uses cache
    monkeypatch.setattr(
        dt_util, "now", lambda: datetime(2025, 6, 16, 19, 59, tzinfo=tz)
    )
    result2 = await coordinator._resolve_solar_forecast(
        "sensor.energy_production_tomorrow"
    )
    assert result2 == pytest.approx(16.0)

    # 20:00 next day — after start hour, should refresh cache with new value
    fake_hass.states.set("sensor.energy_production_tomorrow", "20.0")
    monkeypatch.setattr(dt_util, "now", lambda: datetime(2025, 6, 16, 20, 0, tzinfo=tz))
    result3 = await coordinator._resolve_solar_forecast(
        "sensor.energy_production_tomorrow"
    )
    assert result3 == pytest.approx(20.0)


# ---------------------------------------------------------------------------
# Buy VAT Multiplier Tests
# ---------------------------------------------------------------------------


def test_buy_vat_multiplier_multiplies_buy_price(fake_hass, monkeypatch):
    """buy_vat_multiplier should multiply only the buy price, not raw data."""
    base_time = datetime(2025, 10, 14, 6, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    config[CONF_BUY_VAT_MULTIPLIER] = 1.06
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    # Single future interval at 100 EUR/MWh → 0.1 EUR/kWh raw
    prices_today = {
        "BE": [
            _make_price_interval(base_time + timedelta(minutes=15), 100.0),
        ]
    }

    result = coordinator._calculate_average_threshold(prices_today, None, None)
    # (0.1 + 0) * 1.06 = 0.106
    assert result == pytest.approx(0.106, rel=1e-6)


def test_buy_vat_multiplier_default_is_1_06(fake_hass, monkeypatch):
    """Default buy_vat_multiplier should be 1.06."""
    config = _base_config()
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    vat = coordinator.config.get(CONF_BUY_VAT_MULTIPLIER, DEFAULT_BUY_VAT_MULTIPLIER)
    assert vat == 1.06


def test_buy_vat_multiplier_does_not_affect_feedin(monkeypatch):
    """Feed-in prices should NOT be multiplied by buy_vat_multiplier."""
    # Ensure the price_timeline builder's build_feedin does NOT reference
    # buy_vat_multiplier at all.
    # Check the build_feedin source doesn't contain buy_vat_multiplier
    import inspect

    from custom_components.electricity_planner.price_timeline import (
        PriceTimelineBuilder,
    )

    source = inspect.getsource(PriceTimelineBuilder.build_feedin)
    assert "buy_vat" not in source.lower()


def test_buy_vat_multiplier_custom_value(fake_hass, monkeypatch):
    """A custom buy_vat_multiplier should be applied correctly."""
    base_time = datetime(2025, 10, 14, 6, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    config[CONF_BUY_VAT_MULTIPLIER] = 1.21  # 21% VAT
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    prices_today = {
        "BE": [
            _make_price_interval(base_time + timedelta(minutes=15), 200.0),
        ]
    }

    result = coordinator._calculate_average_threshold(prices_today, None, None)
    # (0.2 + 0) * 1.21 = 0.242
    assert result == pytest.approx(0.242, rel=1e-6)


# --- virtual battery-energy ledger ----------------------------------------


class _MemoryLedgerStore:
    """Store double recording immediate and coalesced delayed writes."""

    def __init__(self, initial_data: dict | None = None):
        self.data = initial_data
        self.immediate_saves = 0
        self.delayed_saves = 0
        self.last_delay: float | None = None

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.immediate_saves += 1
        self.data = data

    def async_delay_save(self, data_func, delay=0):
        self.delayed_saves += 1
        self.last_delay = delay
        self.data = data_func()


def _movable_time(monkeypatch, start):
    """Freeze time at ``start`` and return a mutable reference to advance it."""
    now_ref = {"value": start}
    monkeypatch.setattr(
        coordinator_module.dt_util, "utcnow", lambda: now_ref["value"], raising=False
    )
    monkeypatch.setattr(
        coordinator_module.dt_util, "now", lambda: now_ref["value"], raising=False
    )
    return now_ref


def _fleet(*specs):
    """Build ``battery_details`` for (entity_id, soc, capacity) triples."""
    return [
        {
            "entity_id": entity_id,
            "soc": soc,
            "capacity": capacity,
            "phases": ["phase_1"],
        }
        for entity_id, soc, capacity in specs
    ]


_SAME_SOC = object()


def _full_fleet(soc_1, soc_2=_SAME_SOC, capacity=10.0):
    """Both configured batteries, which is what a valid observation requires."""
    return _fleet(
        ("sensor.battery_soc_1", soc_1, capacity),
        ("sensor.battery_soc_2", soc_1 if soc_2 is _SAME_SOC else soc_2, capacity),
    )


def _ledger_coordinator(fake_hass, monkeypatch, config=None):
    coordinator = _create_coordinator(fake_hass, config or _base_config(), monkeypatch)
    coordinator._energy_ledger_store = _MemoryLedgerStore()
    return coordinator


def _lot_tuples(coordinator):
    return [
        (lot.origin, pytest.approx(lot.energy_kwh), lot.buy_cost_eur_per_kwh)
        for lot in coordinator.energy_ledger.lots
    ]


def _price_analysis(raw_price, transport_cost=0.0, **overrides):
    """Build the engine's price analysis for a raw (pre-VAT) market price."""
    try:
        # Mirrors the engine: an unusable raw price leaves no current price.
        current_price = float(raw_price) + transport_cost
    except (TypeError, ValueError):
        current_price = None
    analysis = {
        "raw_current_price": raw_price,
        "current_price": current_price,
        "price_adjustment_multiplier": 1.0,
        "price_adjustment_offset": 0.0,
        "transport_cost": transport_cost,
        "data_available": True,
    }
    analysis.update(overrides)
    return analysis


def _all_in(raw_price, transport_cost=0.0, vat=DEFAULT_BUY_VAT_MULTIPLIER):
    """Return the VAT-inclusive all-in buy price a lot must record."""
    return (raw_price + transport_cost) * vat


def _grid_intent(coordinator, raw_price, transport_cost=0.0, **analysis_overrides):
    """Record the intent a commanded grid charge at ``raw_price`` would leave."""
    coordinator._record_ledger_charge_intent(
        {
            "price_analysis": _price_analysis(
                raw_price, transport_cost, **analysis_overrides
            )
        },
        {"battery_grid_charging": True},
        True,
        set(),
    )


def _solar_intent(coordinator, allocated_w, grid_power=0.0):
    """Record a solar intent; ``grid_power`` is the raw P1 sign convention."""
    coordinator._record_ledger_charge_intent(
        {"price_analysis": _price_analysis(0.10), "grid_power": grid_power},
        {
            "battery_grid_charging": False,
            "power_allocation": {"solar_for_batteries": allocated_w},
        },
        False,
        set(),
    )


def test_energy_ledger_first_observation_is_entirely_unknown(fake_hass, monkeypatch):
    """A SOC reading proves quantity, never origin."""
    _movable_time(monkeypatch, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc))
    coordinator = _ledger_coordinator(fake_hass, monkeypatch)

    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(50)})

    assert coordinator.energy_ledger_initialized is True
    assert coordinator.energy_ledger_status == "initialized"
    assert _lot_tuples(coordinator) == [
        (EnergyOrigin.UNKNOWN, pytest.approx(10.0), None)
    ]
    assert coordinator._energy_ledger_store.delayed_saves == 1


@pytest.mark.parametrize(
    "details",
    [
        # One configured battery stopped reporting.
        _fleet(("sensor.battery_soc_1", 50, 10.0)),
        # An unusable SOC.
        _full_fleet(50, 150),
        _full_fleet(50, None),
        _full_fleet(50, "half"),
        # An unusable capacity.
        _fleet(
            ("sensor.battery_soc_1", 50, 10.0),
            ("sensor.battery_soc_2", 50, 0),
        ),
        # A duplicated entity.
        _fleet(
            ("sensor.battery_soc_1", 50, 10.0),
            ("sensor.battery_soc_1", 50, 10.0),
        ),
        # Nothing usable at all.
        [],
        None,
        "battery",
        [None],
    ],
)
def test_energy_ledger_never_mutates_on_incomplete_battery_data(
    fake_hass, monkeypatch, details
):
    """A dropout must not look like a discharge and consume lots."""
    now_ref = _movable_time(
        monkeypatch, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    )
    coordinator = _ledger_coordinator(fake_hass, monkeypatch)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(50)})
    _solar_intent(coordinator, 3000)
    now_ref["value"] += timedelta(minutes=10)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(55)})
    before = _lot_tuples(coordinator)
    baseline = coordinator._ledger_last_energy_kwh
    observed_at = coordinator._ledger_last_observed_at
    saves = coordinator._energy_ledger_store.delayed_saves

    now_ref["value"] += timedelta(minutes=10)
    coordinator._reconcile_energy_ledger({"battery_details": details})

    assert coordinator.energy_ledger_status == "battery_data_unavailable"
    assert _lot_tuples(coordinator) == before
    assert coordinator._ledger_last_energy_kwh == baseline
    assert coordinator._ledger_last_observed_at == observed_at
    assert coordinator._energy_ledger_store.delayed_saves == saves


def test_energy_ledger_resets_when_battery_capacity_topology_changes(
    fake_hass, monkeypatch
):
    """A different SOC-to-kWh mapping makes every existing lot meaningless."""
    now_ref = _movable_time(
        monkeypatch, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    )
    coordinator = _ledger_coordinator(fake_hass, monkeypatch)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(50)})
    _grid_intent(coordinator, 0.10)
    now_ref["value"] += timedelta(minutes=10)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(55)})
    assert any(
        lot.origin is EnergyOrigin.GRID for lot in coordinator.energy_ledger.lots
    )

    now_ref["value"] += timedelta(minutes=10)
    coordinator._reconcile_energy_ledger(
        {"battery_details": _full_fleet(55, capacity=15.0)}
    )

    assert coordinator.energy_ledger_status == "inventory_changed"
    assert _lot_tuples(coordinator) == [
        (EnergyOrigin.UNKNOWN, pytest.approx(16.5), None)
    ]
    assert coordinator._ledger_charge_intent is None


def test_energy_ledger_attributes_a_commanded_grid_charge_at_the_all_in_price(
    fake_hass, monkeypatch
):
    now_ref = _movable_time(
        monkeypatch, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    )
    coordinator = _ledger_coordinator(fake_hass, monkeypatch)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(50)})
    _grid_intent(coordinator, 0.1234)

    now_ref["value"] += timedelta(minutes=10)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(52)})

    assert coordinator.energy_ledger_status == "charged_grid"
    assert _lot_tuples(coordinator) == [
        (EnergyOrigin.UNKNOWN, pytest.approx(10.0), None),
        (EnergyOrigin.GRID, pytest.approx(0.4), pytest.approx(_all_in(0.1234))),
    ]


def test_energy_ledger_keeps_a_negative_all_in_purchase_price(fake_hass, monkeypatch):
    """Paid-to-consume energy stays grid-attributed with its negative price."""
    now_ref = _movable_time(
        monkeypatch, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    )
    coordinator = _ledger_coordinator(fake_hass, monkeypatch)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(50)})
    _grid_intent(coordinator, -0.043)

    now_ref["value"] += timedelta(minutes=10)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(52)})

    newest = coordinator.energy_ledger.lots[-1]
    assert newest.origin is EnergyOrigin.GRID
    assert newest.buy_cost_eur_per_kwh == pytest.approx(_all_in(-0.043))


@pytest.mark.parametrize("price", [None, "0.10", float("nan"), True])
def test_energy_ledger_does_not_price_a_grid_charge_without_a_trusted_price(
    fake_hass, monkeypatch, price
):
    now_ref = _movable_time(
        monkeypatch, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    )
    coordinator = _ledger_coordinator(fake_hass, monkeypatch)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(50)})
    _grid_intent(coordinator, price)
    assert coordinator._ledger_charge_intent is None

    now_ref["value"] += timedelta(minutes=10)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(52)})

    assert coordinator.energy_ledger_status == "charged_unattributed"
    assert coordinator.energy_ledger.lots[-1].origin is EnergyOrigin.UNKNOWN


def test_energy_ledger_credits_solar_only_up_to_the_planner_allocation(
    fake_hass, monkeypatch
):
    """Mixed charging cannot be sold as solar beyond the proven allocation."""
    now_ref = _movable_time(
        monkeypatch, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    )
    coordinator = _ledger_coordinator(fake_hass, monkeypatch)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(50)})
    _solar_intent(coordinator, 1000)

    # Ten minutes later the fleet gained 1 kWh, far more than 1 kW could deliver.
    now_ref["value"] += timedelta(minutes=10)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(55)})

    assert coordinator.energy_ledger_status == "charged_solar_partial"
    assert _lot_tuples(coordinator) == [
        (EnergyOrigin.UNKNOWN, pytest.approx(10.0), None),
        (EnergyOrigin.SOLAR, pytest.approx(1.25 / 6), None),
        (EnergyOrigin.UNKNOWN, pytest.approx(1.0 - 1.25 / 6), None),
    ]


def test_energy_ledger_does_not_credit_solar_without_an_allocation(
    fake_hass, monkeypatch
):
    now_ref = _movable_time(
        monkeypatch, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    )
    coordinator = _ledger_coordinator(fake_hass, monkeypatch)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(50)})
    _solar_intent(coordinator, 0)
    assert coordinator._ledger_charge_intent is None

    now_ref["value"] += timedelta(minutes=10)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(52)})

    assert coordinator.energy_ledger.lots[-1].origin is EnergyOrigin.UNKNOWN


def test_energy_ledger_does_not_attribute_a_manually_overridden_charge(
    fake_hass, monkeypatch
):
    """A manual override bypasses the planner, so its energy has no proven origin."""
    now_ref = _movable_time(
        monkeypatch, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    )
    coordinator = _ledger_coordinator(fake_hass, monkeypatch)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(50)})
    coordinator._record_ledger_charge_intent(
        {"price_analysis": {"current_price": 0.10}},
        {"power_allocation": {"solar_for_batteries": 3000}},
        False,
        {"battery_grid_charging"},
    )
    assert coordinator._ledger_charge_intent is None

    now_ref["value"] += timedelta(minutes=10)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(52)})

    assert coordinator.energy_ledger_status == "charged_unattributed"
    assert coordinator.energy_ledger.lots[-1].origin is EnergyOrigin.UNKNOWN


def test_energy_ledger_resets_every_lot_after_a_long_observation_gap(
    fake_hass, monkeypatch
):
    """A stalled refresh can span several price intervals, sources and cycles."""
    now_ref = _movable_time(
        monkeypatch, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    )
    coordinator = _ledger_coordinator(fake_hass, monkeypatch)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(50)})
    _grid_intent(coordinator, 0.05)
    now_ref["value"] += timedelta(minutes=10)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(55)})
    assert any(
        lot.origin is EnergyOrigin.GRID for lot in coordinator.energy_ledger.lots
    )
    _grid_intent(coordinator, 0.05)

    now_ref["value"] += timedelta(minutes=31)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(70)})

    # Not merely the delta: the earlier grid lot may have been discharged and
    # replaced several times over while nothing was observed.
    assert coordinator.energy_ledger_status == "observation_gap"
    assert _lot_tuples(coordinator) == [
        (EnergyOrigin.UNKNOWN, pytest.approx(14.0), None)
    ]
    assert coordinator._ledger_charge_intent is None
    assert coordinator._ledger_last_energy_kwh == pytest.approx(14.0)


@pytest.mark.parametrize("soc,expected_total", [(50, 10.0), (40, 8.0), (70, 14.0)])
def test_energy_ledger_gap_reset_precedes_steady_and_discharge_handling(
    fake_hass, monkeypatch, soc, expected_total
):
    """No SOC direction may slip past the staleness reset."""
    now_ref = _movable_time(
        monkeypatch, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    )
    coordinator = _ledger_coordinator(fake_hass, monkeypatch)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(50)})
    _solar_intent(coordinator, 3000)
    now_ref["value"] += timedelta(minutes=10)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(55)})
    assert any(
        lot.origin is EnergyOrigin.SOLAR for lot in coordinator.energy_ledger.lots
    )

    # An unchanged, lower and higher SOC all arrive after the same long gap.
    now_ref["value"] += timedelta(
        seconds=coordinator_module._ENERGY_LEDGER_MAX_ATTRIBUTION_GAP_SECONDS + 1
    )
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(soc)})

    assert coordinator.energy_ledger_status == "observation_gap"
    assert _lot_tuples(coordinator) == [
        (EnergyOrigin.UNKNOWN, pytest.approx(expected_total), None)
    ]


def test_energy_ledger_keeps_attribution_across_a_watched_idle_period(
    fake_hass, monkeypatch
):
    """Continuous observations of an idle battery are not staleness."""
    now_ref = _movable_time(
        monkeypatch, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    )
    coordinator = _ledger_coordinator(fake_hass, monkeypatch)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(50)})
    _grid_intent(coordinator, 0.05)
    now_ref["value"] += timedelta(minutes=10)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(55)})
    lots_before = _lot_tuples(coordinator)

    # Two hours of an untouched battery, observed every five minutes.
    for _ in range(24):
        now_ref["value"] += timedelta(minutes=5)
        coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(55)})

    assert coordinator.energy_ledger_status == "steady"
    assert _lot_tuples(coordinator) == lots_before


def _drift_then_charge(coordinator, now_ref, record_intent, drift_minutes=10):
    """Accumulate sub-quantisation drift, then command a charge one minute later.

    The baseline stays anchored at the first observation while the drift is
    below the quantisation tolerance, so the interval since the baseline is far
    longer than the command that is supposed to explain the next real movement.
    """
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(50)})
    for _ in range(drift_minutes):
        now_ref["value"] += timedelta(minutes=1)
        coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(50.001)})
    assert coordinator.energy_ledger_status == "steady"

    record_intent()
    now_ref["value"] += timedelta(minutes=1)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(55)})


def test_energy_ledger_caps_solar_credit_by_the_age_of_the_command(
    fake_hass, monkeypatch
):
    """Drift held before the command was issued is not solar the command made."""
    now_ref = _movable_time(
        monkeypatch, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    )
    coordinator = _ledger_coordinator(fake_hass, monkeypatch)

    _drift_then_charge(coordinator, now_ref, lambda: _solar_intent(coordinator, 3000))

    # 3 kW for the one minute the command existed, plus the jitter headroom.
    expected = 3.0 * (60 / 3600) * coordinator_module._ENERGY_LEDGER_POWER_TOLERANCE
    solar_kwh = sum(
        lot.energy_kwh
        for lot in coordinator.energy_ledger.lots
        if lot.origin is EnergyOrigin.SOLAR
    )
    assert coordinator.energy_ledger_status == "charged_solar_partial"
    assert solar_kwh == pytest.approx(expected)
    # Without the cap the eleven-minute baseline interval would have credited
    # more than ten times as much.
    assert solar_kwh < 3.0 * (11 * 60 / 3600)


def test_energy_ledger_caps_grid_credit_by_the_age_of_the_command(
    fake_hass, monkeypatch
):
    """The same cap protects a priced grid lot from an over-credited interval."""
    now_ref = _movable_time(
        monkeypatch, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    )
    config = _base_config()
    config["max_battery_power"] = 3000
    coordinator = _ledger_coordinator(fake_hass, monkeypatch, config)

    _drift_then_charge(coordinator, now_ref, lambda: _grid_intent(coordinator, 0.05))

    expected = 3.0 * (60 / 3600) * coordinator_module._ENERGY_LEDGER_POWER_TOLERANCE
    grid_lots = [
        lot for lot in coordinator.energy_ledger.lots if lot.origin is EnergyOrigin.GRID
    ]
    assert coordinator.energy_ledger_status == "charged_grid_partial"
    assert sum(lot.energy_kwh for lot in grid_lots) == pytest.approx(expected)
    # The unattributable remainder is stacked on top, where it blocks the
    # priced energy underneath it.
    assert coordinator.energy_ledger.lots[-1].origin is EnergyOrigin.UNKNOWN


def test_energy_ledger_ignores_a_command_older_than_the_attribution_window(
    fake_hass, monkeypatch
):
    """A command that predates the window explains nothing, even if watched."""
    now_ref = _movable_time(
        monkeypatch, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    )
    coordinator = _ledger_coordinator(fake_hass, monkeypatch)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(50)})
    _solar_intent(coordinator, 3000)

    # Observed all along (no staleness reset), but the command is long stale.
    for _ in range(20):
        now_ref["value"] += timedelta(minutes=1)
        coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(50)})
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(55)})

    assert coordinator.energy_ledger_status == "charged_unattributed"
    assert _lot_tuples(coordinator) == [
        (EnergyOrigin.UNKNOWN, pytest.approx(11.0), None)
    ]


def test_energy_ledger_ignores_a_baseline_older_than_the_attribution_window(
    fake_hass, monkeypatch
):
    """A fresh command cannot explain a net movement measured over 20 minutes."""
    now_ref = _movable_time(
        monkeypatch, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    )
    coordinator = _ledger_coordinator(fake_hass, monkeypatch)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(50)})

    # Watched throughout, so no staleness reset, but nothing moved enough to
    # re-anchor the energy baseline.
    for _ in range(20):
        now_ref["value"] += timedelta(minutes=1)
        coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(50)})

    _solar_intent(coordinator, 3000)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(55)})

    # The delta is the net of 20 minutes that may hide a charge and a
    # discharge, so it stays unknown rather than being partly credited.
    assert coordinator.energy_ledger_status == "charged_unattributed"
    assert _lot_tuples(coordinator) == [
        (EnergyOrigin.UNKNOWN, pytest.approx(11.0), None)
    ]


# --- attribution requires a real, VAT-inclusive charge authorisation --------


def test_energy_ledger_records_the_vat_inclusive_all_in_buy_price(
    fake_hass, monkeypatch
):
    """The lot price is what the kWh cost at the meter, VAT and transport included."""
    _movable_time(monkeypatch, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc))
    config = _base_config()
    config[CONF_BUY_VAT_MULTIPLIER] = 1.21
    coordinator = _ledger_coordinator(fake_hass, monkeypatch, config)

    analysis = _price_analysis(0.10, transport_cost=0.05)
    coordinator._record_ledger_charge_intent(
        {"price_analysis": analysis},
        {"battery_grid_charging": True},
        True,
        set(),
    )

    intent = coordinator._ledger_charge_intent
    assert intent["buy_cost"] == pytest.approx((0.10 + 0.05) * 1.21)
    # The analysis' own current_price carries no VAT on this path; recording it
    # would price the lot below what the energy actually cost.
    assert intent["buy_cost"] > analysis["current_price"]


def test_energy_ledger_applies_the_price_adjustment_and_vat_together(
    fake_hass, monkeypatch
):
    _movable_time(monkeypatch, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc))
    coordinator = _ledger_coordinator(fake_hass, monkeypatch)

    coordinator._record_ledger_charge_intent(
        {
            "price_analysis": _price_analysis(
                0.10,
                transport_cost=0.04,
                price_adjustment_multiplier=1.1,
                price_adjustment_offset=0.01,
            )
        },
        {"battery_grid_charging": True},
        True,
        set(),
    )

    expected = (0.10 * 1.1 + 0.01 + 0.04) * DEFAULT_BUY_VAT_MULTIPLIER
    assert coordinator._ledger_charge_intent["buy_cost"] == pytest.approx(expected)


@pytest.mark.parametrize(
    "analysis",
    [
        {},
        {"raw_current_price": None, "current_price": 0.12},
        # A usable current price is not a usable *raw* price: without the raw
        # value the VAT-inclusive cost cannot be rebuilt.
        {"current_price": 0.12},
        {"raw_current_price": float("nan")},
        {"raw_current_price": 0.1, "price_adjustment_multiplier": "x"},
        {"raw_current_price": 0.1, "transport_cost": float("inf")},
    ],
)
def test_energy_ledger_refuses_a_grid_lot_it_cannot_price_all_in(
    fake_hass, monkeypatch, analysis
):
    _movable_time(monkeypatch, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc))
    coordinator = _ledger_coordinator(fake_hass, monkeypatch)

    coordinator._record_ledger_charge_intent(
        {"price_analysis": analysis},
        {"battery_grid_charging": True},
        True,
        set(),
    )

    assert coordinator._ledger_charge_intent is None


def test_energy_ledger_does_not_credit_solar_while_the_site_imports(
    fake_hass, monkeypatch
):
    """Importing means solar is already short of the house; the battery is on grid."""
    now_ref = _movable_time(
        monkeypatch, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    )
    coordinator = _ledger_coordinator(fake_hass, monkeypatch)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(50)})

    # Raw P1 convention: negative is import.
    _solar_intent(coordinator, 3000, grid_power=-3000)
    assert coordinator._ledger_charge_intent is None

    now_ref["value"] += timedelta(minutes=10)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(52)})

    assert coordinator.energy_ledger_status == "charged_unattributed"
    assert coordinator.energy_ledger.lots[-1].origin is EnergyOrigin.UNKNOWN


def test_energy_ledger_credits_only_the_solar_the_site_is_not_importing(
    fake_hass, monkeypatch
):
    now_ref = _movable_time(
        monkeypatch, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    )
    coordinator = _ledger_coordinator(fake_hass, monkeypatch)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(50)})

    # 3 kW allocated while the site imports 1 kW proves only 2 kW of solar.
    _solar_intent(coordinator, 3000, grid_power=-1000)
    assert coordinator._ledger_charge_intent["power_w"] == pytest.approx(2000)

    now_ref["value"] += timedelta(minutes=10)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(55)})

    expected = 2.0 * (10 / 60) * coordinator_module._ENERGY_LEDGER_POWER_TOLERANCE
    solar_kwh = sum(
        lot.energy_kwh
        for lot in coordinator.energy_ledger.lots
        if lot.origin is EnergyOrigin.SOLAR
    )
    assert solar_kwh == pytest.approx(expected)


def test_energy_ledger_exports_nothing_while_the_site_exports_and_charges(
    fake_hass, monkeypatch
):
    """Exporting to the grid is the case solar credit is actually for."""
    _movable_time(monkeypatch, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc))
    coordinator = _ledger_coordinator(fake_hass, monkeypatch)

    # Raw P1 convention: positive is export.
    _solar_intent(coordinator, 3000, grid_power=2000)

    assert coordinator._ledger_charge_intent["origin"] is EnergyOrigin.SOLAR
    assert coordinator._ledger_charge_intent["power_w"] == pytest.approx(3000)


@pytest.mark.parametrize("grid_power", [None, "unavailable", float("nan"), True])
def test_energy_ledger_does_not_credit_solar_without_a_grid_reading(
    fake_hass, monkeypatch, grid_power
):
    _movable_time(monkeypatch, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc))
    coordinator = _ledger_coordinator(fake_hass, monkeypatch)

    _solar_intent(coordinator, 3000, grid_power=grid_power)

    assert coordinator._ledger_charge_intent is None


def test_energy_ledger_does_not_credit_solar_during_paid_to_consume_import(
    fake_hass, monkeypatch
):
    """Negative Arbitrage Buy fills the battery from the grid by construction."""
    now = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    _movable_time(monkeypatch, now)
    coordinator = _ledger_coordinator(fake_hass, monkeypatch)

    coordinator._record_ledger_charge_intent(
        {
            "price_analysis": _price_analysis(0.10),
            "grid_power": 0.0,
            "negative_buy_mode_active": True,
        },
        {
            "battery_grid_charging": False,
            "power_allocation": {"solar_for_batteries": 3000},
        },
        False,
        set(),
    )

    assert coordinator._ledger_charge_intent is None


@pytest.mark.parametrize(
    "automatic,commanded",
    [(True, False), (False, True)],
)
def test_energy_ledger_refuses_a_contradicted_charge_command(
    fake_hass, monkeypatch, automatic, commanded
):
    """The applied command and the automatic decision must agree to prove anything."""
    _movable_time(monkeypatch, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc))
    coordinator = _ledger_coordinator(fake_hass, monkeypatch)

    coordinator._record_ledger_charge_intent(
        {"price_analysis": _price_analysis(0.10), "grid_power": 0.0},
        {
            "battery_grid_charging": commanded,
            "power_allocation": {"solar_for_batteries": 3000},
        },
        automatic,
        set(),
    )

    assert coordinator._ledger_charge_intent is None


# --- bounded growth over a long run ---------------------------------------


def test_energy_ledger_lot_count_stays_bounded_over_a_long_run(fake_hass, monkeypatch):
    """Days of refreshes must not grow the ledger without limit."""
    now_ref = _movable_time(
        monkeypatch, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    )
    config = _base_config()
    config["max_battery_power"] = 5000
    coordinator = _ledger_coordinator(fake_hass, monkeypatch, config)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(50)})

    soc = 50.0
    for step in range(2000):
        # Alternate a priced grid charge, an unattributable charge and a
        # discharge, at a price that changes every "interval".
        if step % 3 == 0:
            _grid_intent(coordinator, 0.05 + (step % 96) / 1000)
        elif step % 3 == 1:
            coordinator._ledger_charge_intent = None
        soc = min(95.0, soc + 0.2) if step % 3 != 2 else max(20.0, soc - 0.1)
        now_ref["value"] += timedelta(minutes=1)
        coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(soc)})

    assert len(coordinator.energy_ledger.lots) <= energy_ledger_module.MAX_LOTS
    # The ledger still accounts for exactly what the batteries hold.
    assert coordinator.energy_ledger.total_kwh == pytest.approx(
        20.0 * soc / 100, abs=coordinator_module._ENERGY_LEDGER_TOLERANCE_KWH
    )


@pytest.mark.asyncio
async def test_energy_ledger_reloads_after_a_long_run(fake_hass, monkeypatch):
    """Rounding a large, long-running ledger to disk must not discard it."""
    now_ref = _movable_time(
        monkeypatch, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    )
    coordinator = _ledger_coordinator(fake_hass, monkeypatch)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(50)})

    soc = 50.0
    for step in range(600):
        # Prices that never repeat, so every charge appends its own lot.
        _grid_intent(coordinator, 0.05 + step / 100000)
        soc = min(95.0, soc + 0.05) if step % 4 else max(20.0, soc - 0.02)
        now_ref["value"] += timedelta(minutes=1)
        coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(soc)})

    payload = coordinator._energy_ledger_store.data
    assert len(payload["ledger"]["lots"]) > 1

    restored = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    restored._energy_ledger_store = _MemoryLedgerStore(payload)
    await restored._async_load_energy_ledger()

    assert restored.energy_ledger_status == "restored"
    assert restored.energy_ledger_initialized is True
    assert restored.energy_ledger.total_kwh == pytest.approx(
        coordinator.energy_ledger.total_kwh, abs=1e-3
    )


@pytest.mark.asyncio
async def test_energy_ledger_load_allows_only_the_rounding_it_can_explain(
    fake_hass, monkeypatch
):
    """The rounding allowance is bounded by the lot count, not open-ended."""
    lots = [{"origin": "solar", "energy_kwh": 0.5} for _ in range(200)]
    allowance = (
        coordinator_module._ENERGY_LEDGER_TOLERANCE_KWH
        + 200 * energy_ledger_module.ENERGY_STORAGE_ROUNDING_KWH
    )

    def _payload(offset):
        return {
            "ledger": {"version": 1, "lots": lots},
            "observed_energy_kwh": 100.0 + offset,
            "observed_at": "2026-03-01T08:00:00+00:00",
            "inventory": [["sensor.battery_soc_1", 10.0]],
        }

    accepted = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    accepted._energy_ledger_store = _MemoryLedgerStore(_payload(allowance * 0.9))
    await accepted._async_load_energy_ledger()

    rejected = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    rejected._energy_ledger_store = _MemoryLedgerStore(_payload(allowance * 2))
    await rejected._async_load_energy_ledger()

    assert accepted.energy_ledger_status == "restored"
    assert rejected.energy_ledger_status == "invalid_storage"
    assert rejected.energy_ledger_initialized is False


def test_energy_ledger_discharge_consumes_the_newest_lots_first(fake_hass, monkeypatch):
    now_ref = _movable_time(
        monkeypatch, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    )
    coordinator = _ledger_coordinator(fake_hass, monkeypatch)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(50)})
    _grid_intent(coordinator, 0.05)
    now_ref["value"] += timedelta(minutes=10)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(55)})

    now_ref["value"] += timedelta(minutes=10)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(52)})

    assert coordinator.energy_ledger_status == "discharged"
    assert _lot_tuples(coordinator) == [
        (EnergyOrigin.UNKNOWN, pytest.approx(10.0), None),
        (EnergyOrigin.GRID, pytest.approx(0.4), pytest.approx(_all_in(0.05))),
    ]


def test_energy_ledger_stays_pinned_to_the_observed_inventory(fake_hass, monkeypatch):
    """Residual drift becomes unprovable energy, never sellable energy."""
    now_ref = _movable_time(
        monkeypatch, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    )
    coordinator = _ledger_coordinator(fake_hass, monkeypatch)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(50)})
    # Simulate lost accounting: the ledger now under-reports the real battery.
    coordinator.energy_ledger.discharge(4.0)
    _solar_intent(coordinator, 3000)

    # The next real movement re-pins the ledger to the observed inventory.
    now_ref["value"] += timedelta(minutes=10)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(51)})

    assert coordinator.energy_ledger.total_kwh == pytest.approx(10.2)
    assert sum(
        lot.energy_kwh
        for lot in coordinator.energy_ledger.lots
        if lot.origin is EnergyOrigin.UNKNOWN
    ) == pytest.approx(10.0)


def test_energy_ledger_writes_are_coalesced_and_never_immediate(fake_hass, monkeypatch):
    """The ledger moves every refresh and must not write flash every refresh."""
    now_ref = _movable_time(
        monkeypatch, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    )
    coordinator = _ledger_coordinator(fake_hass, monkeypatch)
    store = coordinator._energy_ledger_store

    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(50)})
    now_ref["value"] += timedelta(minutes=10)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(51)})

    assert store.immediate_saves == 0
    assert store.delayed_saves == 2
    assert store.last_delay == coordinator_module._ENERGY_LEDGER_SAVE_DELAY_SECONDS


@pytest.mark.asyncio
async def test_energy_ledger_store_uses_atomic_writes(fake_hass, monkeypatch):
    """A torn ledger file would be discarded and lose all attribution."""
    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    created: list[dict] = []

    class _RecordingStore:
        def __init__(self, hass, version, key, **kwargs):
            created.append({"key": key, **kwargs})

        async def async_load(self):
            return None

    monkeypatch.setattr(coordinator_module, "Store", _RecordingStore)
    monkeypatch.setattr(
        coordinator_module.ElectricityPlannerCoordinator,
        "_setup_entity_listeners",
        lambda self: None,
    )

    await coordinator.async_initialize_persistent_state()

    ledger_store = next(
        item for item in created if item["key"].endswith(".energy_ledger")
    )
    assert ledger_store["atomic_writes"] is True
    assert all(
        "atomic_writes" not in item
        for item in created
        if not item["key"].endswith(".energy_ledger")
    )


@pytest.mark.asyncio
async def test_energy_ledger_survives_a_restart_without_replaying_the_intent(
    fake_hass, monkeypatch
):
    """A restart must not attribute a downtime's charging to a stale source."""
    now_ref = _movable_time(
        monkeypatch, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    )
    coordinator = _ledger_coordinator(fake_hass, monkeypatch)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(50)})
    _grid_intent(coordinator, 0.05)
    now_ref["value"] += timedelta(minutes=10)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(52)})
    payload = coordinator._energy_ledger_store.data

    assert "charge_intent" not in payload
    assert "grid_charge_cost" not in payload
    assert "solar_charge_expected" not in payload

    restored = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    restored._energy_ledger_store = _MemoryLedgerStore(payload)
    await restored._async_load_energy_ledger()

    assert restored.energy_ledger_initialized is True
    assert restored.energy_ledger_status == "restored"
    assert _lot_tuples(restored) == [
        (EnergyOrigin.UNKNOWN, pytest.approx(10.0), None),
        (EnergyOrigin.GRID, pytest.approx(0.4), pytest.approx(_all_in(0.05))),
    ]
    assert restored._ledger_charge_intent is None

    # Energy that appeared while the process was down has no proven origin.
    now_ref["value"] += timedelta(minutes=5)
    restored._reconcile_energy_ledger({"battery_details": _full_fleet(60)})

    assert restored.energy_ledger_status == "charged_unattributed"
    assert restored.energy_ledger.lots[-1].origin is EnergyOrigin.UNKNOWN


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        "corrupt",
        [],
        {},
        {"ledger": None, "observed_energy_kwh": 1, "observed_at": None},
        # Malformed lot.
        {
            "ledger": {"version": 1, "lots": [{"origin": "grid", "energy_kwh": 1}]},
            "observed_energy_kwh": 1,
            "observed_at": "2026-03-01T08:00:00+00:00",
            "inventory": [["sensor.battery_soc_1", 10.0]],
        },
        # Lots no longer sum to the recorded observation.
        {
            "ledger": {
                "version": 1,
                "lots": [{"origin": "solar", "energy_kwh": 1}],
            },
            "observed_energy_kwh": 9.0,
            "observed_at": "2026-03-01T08:00:00+00:00",
            "inventory": [["sensor.battery_soc_1", 10.0]],
        },
        # Missing or unusable inventory fingerprint.
        {
            "ledger": {"version": 1, "lots": []},
            "observed_energy_kwh": 0.0,
            "observed_at": "2026-03-01T08:00:00+00:00",
            "inventory": None,
        },
        {
            "ledger": {"version": 1, "lots": []},
            "observed_energy_kwh": 0.0,
            "observed_at": "2026-03-01T08:00:00+00:00",
            "inventory": [["sensor.battery_soc_1", 0]],
        },
        # Unusable observation.
        {
            "ledger": {"version": 1, "lots": []},
            "observed_energy_kwh": -1,
            "observed_at": "2026-03-01T08:00:00+00:00",
            "inventory": [["sensor.battery_soc_1", 10.0]],
        },
        {
            "ledger": {"version": 1, "lots": []},
            "observed_energy_kwh": 0.0,
            "observed_at": "not-a-timestamp",
            "inventory": [["sensor.battery_soc_1", 10.0]],
        },
    ],
)
async def test_energy_ledger_untrusted_storage_starts_uninitialized(
    fake_hass, monkeypatch, payload
):
    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    coordinator._energy_ledger_store = _MemoryLedgerStore(payload)

    await coordinator._async_load_energy_ledger()

    assert coordinator.energy_ledger_initialized is False
    assert coordinator.energy_ledger.lots == ()
    assert coordinator._ledger_charge_intent is None


@pytest.mark.asyncio
async def test_energy_ledger_load_failure_fails_closed(fake_hass, monkeypatch):
    class _FailingStore:
        async def async_load(self):
            raise OSError("storage unavailable")

    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    coordinator._energy_ledger = BatteryEnergyLedger([])
    coordinator._energy_ledger_store = _FailingStore()

    await coordinator._async_load_energy_ledger()

    assert coordinator.energy_ledger_initialized is False


def test_energy_ledger_diagnostics_expose_origins_and_intent(fake_hass, monkeypatch):
    now_ref = _movable_time(
        monkeypatch, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    )
    coordinator = _ledger_coordinator(fake_hass, monkeypatch)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(50)})
    _grid_intent(coordinator, 0.07)
    now_ref["value"] += timedelta(minutes=10)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(52)})

    diagnostics = coordinator.energy_ledger_diagnostics()

    assert diagnostics["initialized"] is True
    assert diagnostics["status"] == "charged_grid"
    assert diagnostics["lot_count"] == 2
    assert diagnostics["energy_by_origin"] == {
        "unknown": pytest.approx(10.0),
        "grid": pytest.approx(0.4),
    }
    assert diagnostics["total_kwh"] == pytest.approx(10.4)
    assert diagnostics["charge_intent"]["origin"] == "grid"
    assert diagnostics["charge_intent"]["buy_cost_eur_per_kwh"] == pytest.approx(
        _all_in(0.07)
    )
    assert diagnostics["inventory"] == [
        ["sensor.battery_soc_1", 10.0],
        ["sensor.battery_soc_2", 10.0],
    ]


# --- arbitrage export gated by the ledger ---------------------------------


def _arbitrage_config():
    config = _base_config()
    config.update(
        {
            # A single-battery fleet: the plan fails closed unless every
            # configured battery is reporting.
            CONF_BATTERY_SOC_ENTITIES: ["sensor.battery_soc_1"],
            CONF_ARBITRAGE_MODE_RESERVE_SOC: 20,
            "feedin_adjustment_multiplier": 1.0,
            "feedin_adjustment_offset": 0.0,
            "feedin_price_threshold": 0.05,
            "max_battery_power": 2000,
            "max_grid_power": 6000,
        }
    )
    return config


def _arbitrage_data(base_time, soc, capacity=10.0, price=120.0, batteries=None):
    current_window = [
        _make_price_interval(base_time + timedelta(minutes=15 * slot), price)
        for slot in range(2)
    ]
    later_window = [
        _make_price_interval(base_time + timedelta(hours=2, minutes=15 * slot), 110.0)
        for slot in range(4)
    ]
    return {
        "battery_details": (
            _fleet(("sensor.battery_soc_1", soc, capacity))
            if batteries is None
            else batteries
        ),
        "nordpool_prices_today": {"BE": current_window + later_window},
        "nordpool_prices_tomorrow": None,
    }


def _enable_arbitrage(coordinator, base_time):
    coordinator._arbitrage_mode_state = {
        "value": True,
        "reason": "Arbitrage",
        "expires_at": None,
        "set_at": base_time,
    }


def _install_ledger(coordinator, lots, total, inventory=None):
    ledger = BatteryEnergyLedger()
    for origin, energy, cost in lots:
        ledger.add(origin, energy, cost)
    coordinator._energy_ledger = ledger
    coordinator._ledger_initialized = True
    coordinator._ledger_last_energy_kwh = total
    coordinator._ledger_last_observed_at = coordinator_module.dt_util.utcnow()
    coordinator._ledger_baseline_at = coordinator._ledger_last_observed_at
    coordinator._ledger_inventory = (
        (("sensor.battery_soc_1", 10.0),) if inventory is None else inventory
    )
    coordinator._ledger_status = "installed"


def test_arbitrage_export_is_blocked_before_the_ledger_is_initialized(
    fake_hass, monkeypatch
):
    base_time = datetime(2025, 10, 14, 8, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)
    coordinator = _create_coordinator(fake_hass, _arbitrage_config(), monkeypatch)
    _enable_arbitrage(coordinator, base_time)

    plan = coordinator._calculate_arbitrage_mode_plan(_arbitrage_data(base_time, 40))

    assert plan["active"] is False
    assert plan["export_power"] == 0
    assert plan["available_energy_kwh"] == 0.0
    assert "no trusted battery observation yet" in plan["reason"]


def test_arbitrage_export_never_sells_unknown_energy(fake_hass, monkeypatch):
    """The whole inventory is unknown right after the first observation."""
    base_time = datetime(2025, 10, 14, 8, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)
    coordinator = _create_coordinator(fake_hass, _arbitrage_config(), monkeypatch)
    _enable_arbitrage(coordinator, base_time)
    _install_ledger(coordinator, [(EnergyOrigin.UNKNOWN, 4.0, None)], 4.0)

    plan = coordinator._calculate_arbitrage_mode_plan(_arbitrage_data(base_time, 40))

    assert plan["available_energy_kwh"] == pytest.approx(2.0)
    assert plan["ledger_eligible_stored_kwh"] == 0.0
    assert plan["active"] is False
    assert "no attributable battery energy above the reserve" in plan["reason"]


def test_arbitrage_export_sells_grid_energy_above_its_break_even(
    fake_hass, monkeypatch
):
    base_time = datetime(2025, 10, 14, 8, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)
    coordinator = _create_coordinator(fake_hass, _arbitrage_config(), monkeypatch)
    _enable_arbitrage(coordinator, base_time)
    # 2 kWh of unknown reserve energy below 2 kWh of cheap grid energy.
    _install_ledger(
        coordinator,
        [(EnergyOrigin.UNKNOWN, 2.0, None), (EnergyOrigin.GRID, 2.0, 0.05)],
        4.0,
    )

    plan = coordinator._calculate_arbitrage_mode_plan(_arbitrage_data(base_time, 40))

    assert plan["current_slot_price"] == pytest.approx(0.12)
    assert plan["ledger_reserve_kwh"] == pytest.approx(2.0)
    assert plan["ledger_eligible_stored_kwh"] == pytest.approx(2.0)
    # Only the round-trip share of stored energy is budgeted to the grid.
    assert plan["ledger_exportable_kwh"] == pytest.approx(1.8)
    assert plan["active"] is True
    assert plan["export_power"] == 2000


def test_arbitrage_export_refuses_grid_energy_below_its_break_even(
    fake_hass, monkeypatch
):
    base_time = datetime(2025, 10, 14, 8, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)
    coordinator = _create_coordinator(fake_hass, _arbitrage_config(), monkeypatch)
    _enable_arbitrage(coordinator, base_time)
    # 0.15/0.9 + 0.02 = 0.187 €/kWh required, above the 0.12 €/kWh slot price.
    _install_ledger(
        coordinator,
        [(EnergyOrigin.UNKNOWN, 2.0, None), (EnergyOrigin.GRID, 2.0, 0.15)],
        4.0,
    )

    plan = coordinator._calculate_arbitrage_mode_plan(_arbitrage_data(base_time, 40))

    assert plan["ledger_exportable_kwh"] == 0.0
    assert plan["active"] is False
    assert plan["export_power"] == 0


def test_arbitrage_export_sells_energy_bought_at_a_negative_price(
    fake_hass, monkeypatch
):
    base_time = datetime(2025, 10, 14, 8, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)
    config = _arbitrage_config()
    # Well above the required -0.033 €/kWh, but a positive-cost lot at the same
    # size would need 0.187 €/kWh and stay blocked.
    config["feedin_price_threshold"] = 0.05
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)
    _enable_arbitrage(coordinator, base_time)
    _install_ledger(
        coordinator,
        [(EnergyOrigin.UNKNOWN, 2.0, None), (EnergyOrigin.GRID, 2.0, -0.05)],
        4.0,
    )

    plan = coordinator._calculate_arbitrage_mode_plan(_arbitrage_data(base_time, 40))

    assert plan["active"] is True
    assert plan["ledger_eligible_stored_kwh"] == pytest.approx(2.0)


def test_arbitrage_export_power_is_capped_by_the_eligible_energy(
    fake_hass, monkeypatch
):
    """A small eligible amount must not be dumped at the full inverter cap."""
    base_time = datetime(2025, 10, 14, 8, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)
    coordinator = _create_coordinator(fake_hass, _arbitrage_config(), monkeypatch)
    _enable_arbitrage(coordinator, base_time)
    _install_ledger(
        coordinator,
        [(EnergyOrigin.UNKNOWN, 2.0, None), (EnergyOrigin.SOLAR, 0.05, None)],
        2.05,
    )

    plan = coordinator._calculate_arbitrage_mode_plan(_arbitrage_data(base_time, 20.5))

    assert plan["ledger_exportable_kwh"] == pytest.approx(0.045)
    assert plan["active"] is True
    assert plan["export_power"] == 1350


def test_arbitrage_export_offers_no_more_than_the_ledger_accounts_for(
    fake_hass, monkeypatch
):
    """SOC may claim more headroom than the ledger can attribute."""
    base_time = datetime(2025, 10, 14, 8, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)
    coordinator = _create_coordinator(fake_hass, _arbitrage_config(), monkeypatch)
    _enable_arbitrage(coordinator, base_time)
    _install_ledger(
        coordinator,
        [(EnergyOrigin.UNKNOWN, 2.0, None), (EnergyOrigin.SOLAR, 0.5, None)],
        2.5,
    )

    # SOC says 4 kWh sits above the 20% reserve; the ledger only knows 0.5 kWh.
    plan = coordinator._calculate_arbitrage_mode_plan(_arbitrage_data(base_time, 60))

    assert plan["available_energy_kwh"] == pytest.approx(0.5)
    assert plan["ledger_eligible_stored_kwh"] == pytest.approx(0.5)


@pytest.mark.parametrize(
    "setting,value",
    [
        ("arbitrage_round_trip_efficiency", 0),
        ("arbitrage_round_trip_efficiency", 1.5),
        ("arbitrage_round_trip_efficiency", "high"),
        ("arbitrage_degradation_cost", -0.01),
        ("arbitrage_minimum_profit", -0.01),
    ],
)
def test_arbitrage_export_is_blocked_by_unusable_cost_settings(
    fake_hass, monkeypatch, setting, value
):
    base_time = datetime(2025, 10, 14, 8, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)
    config = _arbitrage_config()
    config[setting] = value
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)
    _enable_arbitrage(coordinator, base_time)
    _install_ledger(
        coordinator,
        [(EnergyOrigin.UNKNOWN, 2.0, None), (EnergyOrigin.SOLAR, 2.0, None)],
        4.0,
    )

    plan = coordinator._calculate_arbitrage_mode_plan(_arbitrage_data(base_time, 40))

    assert plan["active"] is False
    assert "invalid arbitrage cost settings" in plan["reason"]


def _two_battery_arbitrage_config():
    config = _arbitrage_config()
    config[CONF_BATTERY_SOC_ENTITIES] = [
        "sensor.battery_soc_1",
        "sensor.battery_soc_2",
    ]
    return config


_TWO_BATTERY_INVENTORY = (
    ("sensor.battery_soc_1", 10.0),
    ("sensor.battery_soc_2", 10.0),
)


@pytest.mark.parametrize(
    "batteries",
    [
        # One configured battery stopped reporting: its share of the reserve
        # would silently disappear while the lots still account for it.
        _fleet(("sensor.battery_soc_1", 40, 10.0)),
        # An unusable SOC or capacity in the fleet.
        _fleet(
            ("sensor.battery_soc_1", 40, 10.0),
            ("sensor.battery_soc_2", None, 10.0),
        ),
        _fleet(
            ("sensor.battery_soc_1", 40, 10.0),
            ("sensor.battery_soc_2", 140, 10.0),
        ),
        _fleet(
            ("sensor.battery_soc_1", 40, 10.0),
            ("sensor.battery_soc_2", 40, 0),
        ),
        # A battery that is not configured at all.
        _fleet(
            ("sensor.battery_soc_1", 40, 10.0),
            ("sensor.battery_soc_3", 40, 10.0),
        ),
        [],
    ],
)
def test_arbitrage_export_fails_closed_on_an_incomplete_fleet(
    fake_hass, monkeypatch, batteries
):
    """A partial fleet computes a smaller reserve than the ledger accounts for."""
    base_time = datetime(2025, 10, 14, 8, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)
    coordinator = _create_coordinator(
        fake_hass, _two_battery_arbitrage_config(), monkeypatch
    )
    _enable_arbitrage(coordinator, base_time)
    _install_ledger(
        coordinator,
        [(EnergyOrigin.UNKNOWN, 4.0, None), (EnergyOrigin.GRID, 4.0, _all_in(0.05))],
        8.0,
        inventory=_TWO_BATTERY_INVENTORY,
    )

    plan = coordinator._calculate_arbitrage_mode_plan(
        _arbitrage_data(base_time, 40, batteries=batteries)
    )

    assert plan["active"] is False
    assert plan["export_power"] == 0
    assert plan["available_energy_kwh"] == 0.0
    assert plan["ledger_reserve_kwh"] == 0.0
    assert "battery data is incomplete" in plan["reason"]


def test_arbitrage_reserve_covers_every_configured_battery(fake_hass, monkeypatch):
    """The reserve floor is charged against the same fleet the ledger accounts for."""
    base_time = datetime(2025, 10, 14, 8, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)
    coordinator = _create_coordinator(
        fake_hass, _two_battery_arbitrage_config(), monkeypatch
    )
    _enable_arbitrage(coordinator, base_time)
    _install_ledger(
        coordinator,
        [(EnergyOrigin.UNKNOWN, 4.0, None), (EnergyOrigin.GRID, 4.0, _all_in(0.05))],
        8.0,
        inventory=_TWO_BATTERY_INVENTORY,
    )

    plan = coordinator._calculate_arbitrage_mode_plan(
        _arbitrage_data(
            base_time,
            40,
            batteries=_fleet(
                ("sensor.battery_soc_1", 40, 10.0),
                ("sensor.battery_soc_2", 40, 10.0),
            ),
        )
    )

    # 20% of 20 kWh, not of the single battery that a partial fleet would see.
    assert plan["ledger_reserve_kwh"] == pytest.approx(4.0)
    assert plan["available_energy_kwh"] == pytest.approx(4.0)
    assert plan["ledger_eligible_stored_kwh"] == pytest.approx(4.0)
    assert plan["active"] is True


def test_arbitrage_export_is_blocked_when_the_ledger_describes_another_fleet(
    fake_hass, monkeypatch
):
    """Lots accounted against different batteries cannot price this one."""
    base_time = datetime(2025, 10, 14, 8, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)
    coordinator = _create_coordinator(fake_hass, _arbitrage_config(), monkeypatch)
    _enable_arbitrage(coordinator, base_time)
    _install_ledger(
        coordinator,
        [(EnergyOrigin.UNKNOWN, 2.0, None), (EnergyOrigin.GRID, 2.0, _all_in(0.05))],
        4.0,
        inventory=(("sensor.battery_soc_1", 15.0),),
    )

    plan = coordinator._calculate_arbitrage_mode_plan(_arbitrage_data(base_time, 40))

    assert plan["active"] is False
    assert "accounted against a different battery fleet" in plan["reason"]


def test_arbitrage_export_prices_grid_energy_with_vat_included(fake_hass, monkeypatch):
    """A feed-in price between the ex-VAT and all-in break-even must not export."""
    base_time = datetime(2025, 10, 14, 8, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)
    # 0.10 €/kWh ex-VAT breaks even at 0.131 €/kWh, all-in at 0.138 €/kWh.
    slot_price = 135.0
    coordinator = _create_coordinator(fake_hass, _arbitrage_config(), monkeypatch)
    _enable_arbitrage(coordinator, base_time)
    _install_ledger(
        coordinator,
        [(EnergyOrigin.UNKNOWN, 2.0, None), (EnergyOrigin.GRID, 2.0, _all_in(0.10))],
        4.0,
    )

    plan = coordinator._calculate_arbitrage_mode_plan(
        _arbitrage_data(base_time, 40, price=slot_price)
    )

    assert plan["current_slot_price"] == pytest.approx(0.135)
    assert plan["ledger_exportable_kwh"] == 0.0
    assert plan["active"] is False

    # The same energy priced without VAT would have been sold at a loss.
    ex_vat = _create_coordinator(fake_hass, _arbitrage_config(), monkeypatch)
    _enable_arbitrage(ex_vat, base_time)
    _install_ledger(
        ex_vat,
        [(EnergyOrigin.UNKNOWN, 2.0, None), (EnergyOrigin.GRID, 2.0, 0.10)],
        4.0,
    )

    loss_making = ex_vat._calculate_arbitrage_mode_plan(
        _arbitrage_data(base_time, 40, price=slot_price)
    )

    assert loss_making["active"] is True


@pytest.mark.asyncio
async def test_full_update_cycle_records_intent_then_attributes_the_next_delta(
    fake_hass, monkeypatch
):
    """End to end: a commanded grid charge prices the energy it produces."""
    now_ref = _movable_time(
        monkeypatch, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    )
    coordinator = _ledger_coordinator(fake_hass, monkeypatch)
    coordinator._check_data_availability = AsyncMock()
    fleet = {"value": _full_fleet(50)}
    coordinator._fetch_all_data = AsyncMock(
        side_effect=lambda: {
            "current_price": 0.11,
            "battery_details": fleet["value"],
        }
    )
    coordinator.decision_engine.evaluate_charging_decision = AsyncMock(
        return_value={
            "battery_grid_charging": True,
            "price_analysis": _price_analysis(0.0875),
        }
    )

    first = await coordinator._async_update_data()

    # The first observation can only establish quantity.
    assert first["energy_ledger"]["status"] == "initialized"
    assert _lot_tuples(coordinator) == [
        (EnergyOrigin.UNKNOWN, pytest.approx(10.0), None)
    ]
    assert coordinator._ledger_charge_intent["origin"] is EnergyOrigin.GRID
    assert coordinator._ledger_charge_intent["buy_cost"] == pytest.approx(
        _all_in(0.0875)
    )

    now_ref["value"] += timedelta(minutes=10)
    fleet["value"] = _full_fleet(52)
    second = await coordinator._async_update_data()

    assert second["energy_ledger"]["status"] == "charged_grid"
    assert _lot_tuples(coordinator) == [
        (EnergyOrigin.UNKNOWN, pytest.approx(10.0), None),
        (EnergyOrigin.GRID, pytest.approx(0.4), pytest.approx(_all_in(0.0875))),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "grid_power,expected_status",
    [
        # Exporting: the battery is demonstrably filling from surplus solar.
        (1500.0, "charged_solar"),
        # Importing more than the allocation: the energy came off the grid.
        (-4000.0, "charged_unattributed"),
        # No grid reading at all.
        (None, "charged_unattributed"),
    ],
)
async def test_full_update_cycle_credits_solar_only_against_the_grid_reading(
    fake_hass, monkeypatch, grid_power, expected_status
):
    """End to end: a solar allocation alone never proves the battery's source."""
    now_ref = _movable_time(
        monkeypatch, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    )
    coordinator = _ledger_coordinator(fake_hass, monkeypatch)
    coordinator._check_data_availability = AsyncMock()
    fleet = {"value": _full_fleet(50)}
    coordinator._fetch_all_data = AsyncMock(
        side_effect=lambda: {
            "current_price": 0.11,
            "grid_power": grid_power,
            "battery_details": fleet["value"],
        }
    )
    coordinator.decision_engine.evaluate_charging_decision = AsyncMock(
        return_value={
            "battery_grid_charging": False,
            "power_allocation": {"solar_for_batteries": 3000},
            "price_analysis": _price_analysis(0.09),
        }
    )

    await coordinator._async_update_data()

    now_ref["value"] += timedelta(minutes=1)
    fleet["value"] = _full_fleet(50.2)
    result = await coordinator._async_update_data()

    assert result["energy_ledger"]["status"] == expected_status


@pytest.mark.asyncio
async def test_full_update_cycle_does_not_attribute_a_manual_override_charge(
    fake_hass, monkeypatch
):
    """A forced battery charge is not proof of a planner-chosen source."""
    now_ref = _movable_time(
        monkeypatch, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    )
    coordinator = _ledger_coordinator(fake_hass, monkeypatch)
    coordinator._check_data_availability = AsyncMock()
    fleet = {"value": _full_fleet(50)}
    coordinator._fetch_all_data = AsyncMock(
        side_effect=lambda: {
            "current_price": 0.11,
            "battery_details": fleet["value"],
        }
    )
    coordinator.decision_engine.evaluate_charging_decision = AsyncMock(
        return_value={
            "battery_grid_charging": False,
            "power_allocation": {"solar_for_batteries": 3000},
            "price_analysis": _price_analysis(0.09),
        }
    )
    coordinator._apply_manual_overrides = lambda decision: (
        {**decision, "battery_grid_charging": True},
        {"battery_grid_charging"},
    )
    coordinator.decision_engine.recalculate_after_override = (
        lambda data, decision, targets: decision
    )

    await coordinator._async_update_data()
    assert coordinator._ledger_charge_intent is None

    now_ref["value"] += timedelta(minutes=10)
    fleet["value"] = _full_fleet(52)
    result = await coordinator._async_update_data()

    assert result["energy_ledger"]["status"] == "charged_unattributed"
    assert coordinator.energy_ledger.lots[-1].origin is EnergyOrigin.UNKNOWN


def test_energy_ledger_steady_observation_does_not_schedule_a_write(
    fake_hass, monkeypatch
):
    """SOC noise must not turn into a flash write, and must not drift the baseline."""
    now_ref = _movable_time(
        monkeypatch, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    )
    coordinator = _ledger_coordinator(fake_hass, monkeypatch)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(50)})
    store = coordinator._energy_ledger_store
    saves = store.delayed_saves
    baseline = coordinator._ledger_last_energy_kwh
    baseline_at = coordinator._ledger_baseline_at

    # 0.001% of 20 kWh is 0.0002 kWh, below the quantisation tolerance.
    now_ref["value"] += timedelta(minutes=10)
    coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(50.001)})

    assert coordinator.energy_ledger_status == "steady"
    assert store.delayed_saves == saves
    assert coordinator._ledger_last_energy_kwh == baseline
    assert coordinator._ledger_baseline_at == baseline_at
    # The observation itself is accepted, so an idle battery never ages into
    # the staleness reset while the planner is watching it.
    assert coordinator._ledger_last_observed_at == now_ref["value"]


def test_energy_ledger_persisted_lots_always_match_the_observation(
    fake_hass, monkeypatch
):
    """The load-time consistency check must never reject our own writes."""
    now_ref = _movable_time(
        monkeypatch, datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
    )
    coordinator = _ledger_coordinator(fake_hass, monkeypatch)
    store = coordinator._energy_ledger_store

    for step, soc in enumerate([50, 55, 52, 52.0005, 61, 40]):
        _grid_intent(coordinator, 0.05 + step / 100)
        now_ref["value"] += timedelta(minutes=5)
        coordinator._reconcile_energy_ledger({"battery_details": _full_fleet(soc)})
        payload = store.data
        total = sum(lot["energy_kwh"] for lot in payload["ledger"]["lots"])
        assert total == pytest.approx(
            payload["observed_energy_kwh"],
            abs=coordinator_module._ENERGY_LEDGER_TOLERANCE_KWH,
        )


def test_unavailable_battery_is_emitted_as_placeholder(fake_hass, monkeypatch):
    """_fetch_all_data must emit unavailable batteries, not omit them.

    BatteryAnalysisCalculator keys its held-SOC cache by entity_id, so a battery
    that is simply absent from the payload cannot be held. Omitting it silently
    re-weights the capacity-weighted average onto the remaining batteries, which
    is exactly the threshold flip this contract exists to prevent.
    """
    config = _base_config()
    config[CONF_BATTERY_SOC_ENTITIES] = [
        "sensor.battery_soc_1",
        "sensor.battery_soc_2",
    ]
    config[CONF_BATTERY_CAPACITIES] = {
        "sensor.battery_soc_1": 30.0,
        "sensor.battery_soc_2": 10.0,
    }
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    socs = {"sensor.battery_soc_1": 47.0, "sensor.battery_soc_2": 8.0}

    async def fake_state_value(entity_id):
        return socs.get(entity_id)

    monkeypatch.setattr(coordinator, "_get_state_value", fake_state_value)

    # Prime the hold cache with both batteries present.
    primed = asyncio.run(coordinator._fetch_all_data())
    assert [b["soc"] for b in primed["battery_soc"]] == [47.0, 8.0]

    # Now the small battery's sensor goes unavailable.
    socs["sensor.battery_soc_2"] = None
    held = asyncio.run(coordinator._fetch_all_data())

    published = {b["entity_id"]: b["soc"] for b in held["battery_soc"]}
    assert published == {
        "sensor.battery_soc_1": 47.0,
        "sensor.battery_soc_2": 8.0,
    }, "unavailable battery must keep its held SOC instead of disappearing"

    # battery_details feeds negative_buy.py, which coerces a missing SOC to 0.0.
    details = {b["entity_id"]: b["soc"] for b in held["battery_details"]}
    assert details["sensor.battery_soc_2"] == 8.0
    assert all(v is not None for v in details.values())


def test_battery_threshold_snapshot_refreshes_when_source_changes(
    fake_hass, monkeypatch
):
    """A fallback snapshot must not stay pinned once the average is available."""
    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    _freeze_time(monkeypatch, datetime(2026, 10, 24, 16, 47, tzinfo=timezone.utc))

    # First cycle after restart: average unavailable → configured 0.25 fallback.
    coordinator._update_battery_threshold_snapshot_if_needed(0.25, "configured")
    assert coordinator._battery_threshold_snapshot == 0.25

    # Same interval, average now available: snapshot follows the stricter value.
    _freeze_time(monkeypatch, datetime(2026, 10, 24, 16, 49, tzinfo=timezone.utc))
    coordinator._update_battery_threshold_snapshot_if_needed(0.1584, "average")
    assert coordinator._battery_threshold_snapshot == 0.1584

    # Same source within the interval stays stable (anti-flap behaviour kept).
    coordinator._update_battery_threshold_snapshot_if_needed(0.17, "average")
    assert coordinator._battery_threshold_snapshot == 0.1584


def test_battery_forecast_prices_only_exported_when_fresh(fake_hass, monkeypatch):
    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    now = datetime(2026, 10, 24, 16, 50, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, now)
    past = (now - timedelta(minutes=30), now - timedelta(minutes=15), 0.2)
    current = (now - timedelta(minutes=5), now + timedelta(minutes=10), 0.25)
    future = (now + timedelta(minutes=10), now + timedelta(minutes=25), 0.14)
    coordinator._last_price_timeline = [past, current, future]
    coordinator._last_price_timeline_generated_at = now - timedelta(minutes=2)

    assert coordinator._battery_forecast_prices({"available": True}) == [
        current,
        future,
    ]
    assert (
        coordinator._battery_forecast_prices({"available": True, "stale": True}) == []
    )
    assert coordinator._battery_forecast_prices({"available": False}) == []
    assert coordinator._battery_forecast_prices(None) == []

    # Timeline older than the max age, or with no generation time.
    coordinator._last_price_timeline_generated_at = now - timedelta(hours=2)
    assert coordinator._battery_forecast_prices({"available": True}) == []
    coordinator._last_price_timeline_generated_at = None
    assert coordinator._battery_forecast_prices({"available": True}) == []
    # Generated "in the future" (clock skew) is not trusted either.
    coordinator._last_price_timeline_generated_at = now + timedelta(minutes=5)
    assert coordinator._battery_forecast_prices({"available": True}) == []

    # Fresh build but no interval covers now (e.g. day-old data) → rejected.
    coordinator._last_price_timeline_generated_at = now
    coordinator._last_price_timeline = [past, future]
    assert coordinator._battery_forecast_prices({"available": True}) == []
    coordinator._last_price_timeline = None
    assert coordinator._battery_forecast_prices({"available": True}) == []
