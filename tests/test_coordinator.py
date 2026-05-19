"""Integration-oriented tests for Electricity Planner coordinator."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytz
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.electricity_planner import coordinator as coordinator_module
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
    CONF_CAR_CHARGING_POWER_ENTITY,
    CONF_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
    CONF_CURRENT_PRICE_ENTITY,
    CONF_DYNAMIC_THRESHOLD_CONFIDENCE,
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
            0.135,
        ),
        (
            1.1,
            0.05,
            [
                # Week-old data: what transport cost was 7 days ago at 08:00 and 09:00
                {"start": "2025-10-07T08:00:00+00:00", "cost": 0.02},
                {"start": "2025-10-07T09:00:00+00:00", "cost": 0.03},
            ],
            0.196,
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
    # Only the future interval remains: (100/1000) = 0.1
    assert result == pytest.approx(0.1, rel=1e-6)


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
    # Average of 95 * 0.100 + 1 * 0.200 over 96 slots ≈ 0.10104 -> rounded to 0.101
    assert result == pytest.approx(0.101, rel=1e-6)


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
    # Future-only average: (0.100 + 0.130) / 2 = 0.115 -> rounded to 0.115
    assert result == pytest.approx(0.115, rel=1e-6)


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

    plan = coordinator._calculate_arbitrage_mode_plan(
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
        }
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

    plan = coordinator._calculate_arbitrage_mode_plan(
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
        }
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

    plan = coordinator._calculate_arbitrage_mode_plan(
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
        }
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

    plan = coordinator._calculate_arbitrage_mode_plan(
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
        }
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

    plan = coordinator._calculate_arbitrage_mode_plan(
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
        }
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

    plan = coordinator._calculate_arbitrage_mode_plan(
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
        }
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

    plan = coordinator._calculate_arbitrage_mode_plan(
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
        }
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

    plan = coordinator._calculate_arbitrage_mode_plan(
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
        }
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

    plan = coordinator._calculate_arbitrage_mode_plan(
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
        }
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

    plan = coordinator._calculate_arbitrage_mode_plan(
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
        }
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
    assert plan["buy_price_threshold"] == pytest.approx(-0.10, rel=1e-6)
    assert plan["current_slot_price"] == pytest.approx(-0.10, rel=1e-6)
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
    assert plan["current_slot_price"] == pytest.approx(-0.10, rel=1e-6)
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
    assert summary["cheapest_interval_price"] == pytest.approx(0.04, rel=1e-6)
    parsed_cheapest_start = dt_util.parse_datetime(summary["cheapest_interval_start"])
    assert dt_util.as_utc(parsed_cheapest_start) == base_time + timedelta(hours=2)
    assert summary["best_window_average_price"] == pytest.approx(0.04, rel=1e-6)
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
    assert summary["cheapest_interval_price"] == pytest.approx(-0.005, rel=1e-6)
    assert summary["best_window_average_price"] == pytest.approx(-0.005, rel=1e-6)
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
    assert summary["cheapest_interval_price"] == pytest.approx(0.05, rel=1e-6)
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
    night_transport = (
        DEFAULT_TRANSPORT_COST_NIGHT
        + DEFAULT_ENERGY_TAX_ACCIJNS
        + DEFAULT_ENERGY_TAX_BIJDRAGE
        + DEFAULT_ENERGY_COST_GSC
        + DEFAULT_ENERGY_COST_WKK
    )

    assert overrides is not None
    assert overrides["current_price"] == pytest.approx(0.1 + day_transport, rel=1e-6)
    assert overrides["next_price"] == pytest.approx(0.1 + night_transport, rel=1e-6)
    assert overrides["highest_price"] == pytest.approx(0.15 + night_transport, rel=1e-6)
    assert overrides["lowest_price"] == pytest.approx(0.05 + day_transport, rel=1e-6)
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
