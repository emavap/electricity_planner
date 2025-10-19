"""Integration-oriented tests for Electricity Planner coordinator."""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import pytz

from homeassistant.util import dt as dt_util

from custom_components.electricity_planner import coordinator as coordinator_module
from custom_components.electricity_planner.coordinator import ElectricityPlannerCoordinator
from custom_components.electricity_planner.const import (
    ATTR_ACTION,
    ATTR_ENTRY_ID,
    ATTR_TARGET,
    CONF_BATTERY_SOC_ENTITIES,
    CONF_CAR_CHARGING_POWER_ENTITY,
    CONF_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
    CONF_CURRENT_PRICE_ENTITY,
    CONF_DYNAMIC_THRESHOLD_CONFIDENCE,
    CONF_HIGHEST_PRICE_ENTITY,
    CONF_HOUSE_CONSUMPTION_ENTITY,
    CONF_PHASE_MODE,
    CONF_PHASES,
    CONF_PHASE_SOLAR_ENTITY,
    CONF_PHASE_CONSUMPTION_ENTITY,
    CONF_PHASE_CAR_ENTITY,
    CONF_PHASE_BATTERY_POWER_ENTITY,
    CONF_BATTERY_CAPACITIES,
    CONF_BATTERY_PHASE_ASSIGNMENTS,
    CONF_LOWEST_PRICE_ENTITY,
    CONF_NEXT_PRICE_ENTITY,
    CONF_PRICE_THRESHOLD,
    CONF_SOLAR_PRODUCTION_ENTITY,
    CONF_TRANSPORT_COST_ENTITY,
    CONF_USE_AVERAGE_THRESHOLD,
    DEFAULT_MIN_CAR_CHARGING_DURATION,
    DOMAIN,
    MANUAL_OVERRIDE_ACTION_FORCE_CHARGE,
    MANUAL_OVERRIDE_ACTION_FORCE_WAIT,
    MANUAL_OVERRIDE_TARGET_BATTERY,
    MANUAL_OVERRIDE_TARGET_CAR,
    SERVICE_CLEAR_MANUAL_OVERRIDE,
    SERVICE_SET_MANUAL_OVERRIDE,
    PHASE_MODE_THREE,
)
from homeassistant.exceptions import HomeAssistantError


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
        self.loop = asyncio.get_event_loop()
        self.states = FakeStates()
        self.services = FakeServices()
        self.data: dict = {}
        self.config_entries = SimpleNamespace(_entries={})

    def async_create_task(self, coro):
        return self.loop.create_task(coro)

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
                },
                "phase_2": {
                    CONF_PHASE_SOLAR_ENTITY: "sensor.solar_l2",
                    CONF_PHASE_CONSUMPTION_ENTITY: "sensor.load_l2",
                    CONF_PHASE_CAR_ENTITY: "sensor.car_l2",
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


@pytest.mark.asyncio
async def test_fetch_all_data_computes_surplus_and_filters_unavailable(fake_hass, monkeypatch):
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
    fake_hass.states.set("sensor.solar_l2", "600")
    fake_hass.states.set("sensor.load_l2", "900")
    fake_hass.states.set("sensor.car_l2", "700")
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
    assert phase_details["phase_2"]["solar_surplus"] == 0
    assert phase_details["phase_2"]["car_charging_power"] == pytest.approx(700.0)

    capacity_map = data["phase_capacity_map"]
    assert capacity_map["phase_1"] == pytest.approx(5.0)
    assert capacity_map["phase_2"] == pytest.approx(11.0)
    assert capacity_map["phase_3"] == pytest.approx(0.0)

    phase_batteries = data["phase_batteries"]
    assert [b["entity_id"] for b in phase_batteries["phase_1"]] == ["sensor.battery_soc_1"]
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
async def test_fetch_all_data_three_phase_partial_solar(fake_hass, monkeypatch):
    config = _three_phase_config()
    config[CONF_PHASES]["phase_2"].pop(CONF_PHASE_SOLAR_ENTITY, None)
    config[CONF_PHASES]["phase_3"].pop(CONF_PHASE_SOLAR_ENTITY, None)

    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    fake_hass.states.set("sensor.current_price", "0.18")
    fake_hass.states.set("sensor.highest_price", "0.40")
    fake_hass.states.set("sensor.lowest_price", "0.05")
    fake_hass.states.set("sensor.next_price", "0.15")

    fake_hass.states.set("sensor.battery_soc_1", "55")
    fake_hass.states.set("sensor.battery_soc_2", "65")

    fake_hass.states.set("sensor.solar_l1", "2500")
    fake_hass.states.set("sensor.load_l1", "800")
    fake_hass.states.set("sensor.load_l2", "900")
    fake_hass.states.set("sensor.car_l2", "600")
    fake_hass.states.set("sensor.load_l3", "700")

    data = await coordinator._fetch_all_data()

    assert data["solar_production"] == pytest.approx(2500.0)
    assert data["house_consumption"] == pytest.approx(2400.0)
    assert data["solar_surplus"] == pytest.approx(100.0)
    assert data["phase_details"]["phase_1"]["solar_production"] == pytest.approx(2500.0)
    assert data["phase_details"]["phase_2"]["solar_production"] is None
    assert data["phase_details"]["phase_3"]["solar_production"] is None
    assert data["phase_batteries"]["phase_3"] == []


@pytest.mark.asyncio
async def test_fetch_all_data_three_phase_with_battery_power_sensors(fake_hass, monkeypatch):
    """Test that battery power sensors are correctly read in three-phase mode."""
    config = _three_phase_config()
    # Add battery power sensors to phase configuration
    config[CONF_PHASES]["phase_1"][CONF_PHASE_BATTERY_POWER_ENTITY] = "sensor.battery_power_l1"
    config[CONF_PHASES]["phase_2"][CONF_PHASE_BATTERY_POWER_ENTITY] = "sensor.battery_power_l2"

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
    assert len(tasks) == 1

    clock["now"] = base_time + timedelta(seconds=15)
    coordinator._handle_entity_change(event)
    for task in tasks[1:]:
        await task
    assert len(tasks) == 2
    assert coordinator.async_request_refresh.await_count == 2


@pytest.mark.asyncio
async def test_handle_entity_change_includes_three_phase_entities(fake_hass, monkeypatch):
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
    assert len(tasks) == 1

    clock["now"] = base_time + timedelta(seconds=15)
    coordinator._handle_entity_change(event)
    for task in tasks[1:]:
        await task
    assert len(tasks) == 2
    assert coordinator.async_request_refresh.await_count == 2


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
                {"start": "2025-10-14T00:00:00+00:00", "end": "2025-10-14T00:15:00+00:00", "price": 104.85},
                {"start": "2025-10-14T00:15:00+00:00", "end": "2025-10-14T00:30:00+00:00", "price": 97.53},
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
            {"start": "2025-10-14T00:00:00+00:00", "end": "2025-10-14T00:15:00+00:00", "price": 104.85},
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
async def test_nordpool_handles_service_failure(fake_hass, monkeypatch):
    """Test that Nord Pool service failures are handled gracefully."""
    from custom_components.electricity_planner.const import CONF_NORDPOOL_CONFIG_ENTRY

    config = _base_config()
    config[CONF_NORDPOOL_CONFIG_ENTRY] = "test_config_entry_id"
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    # Mock service to raise exception
    fake_hass.services.async_call = AsyncMock(side_effect=Exception("Service unavailable"))

    result = await coordinator._fetch_nordpool_prices("test_config_entry_id", "today")

    # Should return None instead of crashing
    assert result is None
    fake_hass.services.async_call.assert_awaited_once()


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
    today_prices = {"BE": [{"start": "2025-10-14T10:00:00+00:00", "end": "2025-10-14T10:15:00+00:00", "price": 100.0}]}
    tomorrow_prices = {"BE": [{"start": "2025-10-15T10:00:00+00:00", "end": "2025-10-15T10:15:00+00:00", "price": 110.0}]}
    target_today = base_time.date().isoformat()
    target_tomorrow = (base_time + timedelta(days=1)).date().isoformat()

    call_count = [0]
    async def mock_service_call(domain, service, data, blocking=False, return_response=False):
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
    monkeypatch.setattr(coordinator_module.dt_util, "now", lambda: base_time, raising=False)
    monkeypatch.setattr(coordinator_module.dt_util, "utcnow", lambda: base_time, raising=False)


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
def test_calculate_average_threshold(fake_hass, monkeypatch, multiplier, offset, transport_lookup, expected):
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
            _make_price_interval(base_time + timedelta(minutes=15), 100.0),  # 0.1 €/kWh base
            _make_price_interval(base_time + timedelta(hours=1), 120.0),      # 0.12 €/kWh base
        ]
    }

    result = coordinator._calculate_average_threshold(prices_today, None, transport_lookup)
    assert result == pytest.approx(expected, rel=1e-6)


def test_calculate_average_threshold_skips_past(fake_hass, monkeypatch):
    """Intervals in the past should be ignored."""
    base_time = datetime(2025, 10, 14, 8, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    prices_today = {
        "BE": [
            _make_price_interval(base_time - timedelta(hours=1), 80.0),       # past interval
            _make_price_interval(base_time + timedelta(minutes=30), 100.0),   # future interval
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


def test_calculate_average_threshold_insufficient_past_uses_future_only(fake_hass, monkeypatch):
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

    average = coordinator._calculate_average_threshold(prices_today, None, transport_lookup)

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
        _make_price_interval(base_time + timedelta(minutes=30), 120.0),  # high price breaks window
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


def test_check_minimum_charging_window_single_interval_too_short(fake_hass, monkeypatch):
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

    overridden = coordinator._apply_manual_overrides(decision)
    assert overridden["battery_grid_charging"] is True
    assert "override" in overridden["battery_grid_charging_reason"]
    assert overridden["manual_overrides"]["battery_grid_charging"]["reason"] == "boost"
    assert overridden["strategy_trace"][-1]["strategy"] == "ManualOverride"

    _freeze_time(monkeypatch, base_time + timedelta(minutes=31))
    follow_up = coordinator._apply_manual_overrides(
        {
            "battery_grid_charging": False,
            "battery_grid_charging_reason": "reset",
            "strategy_trace": [],
        }
    )
    assert follow_up["battery_grid_charging"] is False
    assert coordinator._manual_overrides["battery_grid_charging"] is None


def test_forecast_summary_uses_price_timeline(fake_hass, monkeypatch):
    """Forecast summary exposes cheapest interval and best window."""
    base_time = datetime(2025, 10, 14, 8, 0, tzinfo=timezone.utc)
    _freeze_time(monkeypatch, base_time)

    config = _base_config()
    config.update({
        "price_adjustment_multiplier": 1.0,
        "price_adjustment_offset": 0.0,
    })
    coordinator = _create_coordinator(fake_hass, config, monkeypatch)

    intervals = []
    for slot in range(16):
        start = base_time + timedelta(minutes=15 * slot)
        value = 60.0 if slot < 8 else 40.0
        intervals.append(_make_price_interval(start, value))

    prices_today = {"BE": intervals}

    average = coordinator._calculate_average_threshold(prices_today, None, None)
    coordinator._check_minimum_charging_window(
        prices_today, None, None, None, average
    )

    summary = coordinator._calculate_forecast_summary(
        prices_today,
        None,
        None,
        None,
        average,
    )

    assert summary["available"] is True
    assert summary["cheapest_interval_price"] == pytest.approx(0.04, rel=1e-6)
    assert summary["cheapest_interval_start"] == (base_time + timedelta(hours=2)).isoformat()
    assert summary["best_window_average_price"] == pytest.approx(0.04, rel=1e-6)
    assert summary["best_window_start"] == (base_time + timedelta(hours=2)).isoformat()


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

    expected_start = (base_time + timedelta(minutes=60)).isoformat()

    assert summary["available"] is True
    assert summary["cheapest_interval_price"] == pytest.approx(-0.005, rel=1e-6)
    assert summary["best_window_average_price"] == pytest.approx(-0.005, rel=1e-6)
    assert summary["best_window_average_price"] <= 0
    assert summary["best_window_start"] == expected_start


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

    handler = fake_hass.services.registered[(DOMAIN, SERVICE_SET_MANUAL_OVERRIDE)]["handler"]

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

    handler = fake_hass.services.registered[(DOMAIN, SERVICE_SET_MANUAL_OVERRIDE)]["handler"]

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

    handler = fake_hass.services.registered[(DOMAIN, SERVICE_CLEAR_MANUAL_OVERRIDE)]["handler"]

    await handler(
        FakeServiceCall(
            {
                ATTR_ENTRY_ID: coordinator.entry.entry_id,
                ATTR_TARGET: MANUAL_OVERRIDE_TARGET_CAR,
            }
        )
    )

    coordinator.async_clear_manual_override.assert_awaited_once_with(MANUAL_OVERRIDE_TARGET_CAR)
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

        monkeypatch.setitem(sys.modules, "homeassistant.components.recorder", fake_recorder)
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
