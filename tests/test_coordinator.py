"""Integration-oriented tests for Electricity Planner coordinator."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.electricity_planner import coordinator as coordinator_module
from custom_components.electricity_planner.coordinator import ElectricityPlannerCoordinator
from custom_components.electricity_planner.const import (
    CONF_BATTERY_SOC_ENTITIES,
    CONF_CAR_CHARGING_POWER_ENTITY,
    CONF_CURRENT_PRICE_ENTITY,
    CONF_HIGHEST_PRICE_ENTITY,
    CONF_HOUSE_CONSUMPTION_ENTITY,
    CONF_LOWEST_PRICE_ENTITY,
    CONF_NEXT_PRICE_ENTITY,
    CONF_SOLAR_PRODUCTION_ENTITY,
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


class FakeServices:
    def __init__(self):
        self.calls: list[tuple[str, str, dict]] = []

    async def async_call(self, domain, service, data, blocking=False, context=None):
        self.calls.append((domain, service, data))


class FakeHass:
    def __init__(self):
        self.loop = asyncio.get_event_loop()
        self.states = FakeStates()
        self.services = FakeServices()
        self.data: dict = {}
        self.config_entries = SimpleNamespace(_entries={})

    def async_create_task(self, coro):
        return self.loop.create_task(coro)


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


def _create_coordinator(fake_hass, config, monkeypatch):
    entry = MockConfigEntry(domain=DOMAIN, data=config)
    monkeypatch.setattr(
        coordinator_module.ElectricityPlannerCoordinator,
        "_setup_entity_listeners",
        lambda self: None,
    )
    coordinator = ElectricityPlannerCoordinator(fake_hass, entry)
    return coordinator


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

    def capture_task(coro):
        task = fake_hass.async_create_task(coro)
        tasks.append(task)
        return task

    coordinator.async_create_task = capture_task

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
