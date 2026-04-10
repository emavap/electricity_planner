"""Tests for switch platform entities."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.const import STATE_ON
from homeassistant.helpers.update_coordinator import CoordinatorEntity

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
from custom_components.electricity_planner.coordinator import ElectricityPlannerCoordinator
from custom_components.electricity_planner.switch import (
    ArbitrageModeSwitch,
    CarPermissiveModeSwitch,
)


class FakeState:
    """Minimal fake state object."""

    def __init__(self, state: str):
        self.state = state


class FakeStates:
    """Minimal fake state registry."""

    def __init__(self):
        self._states: dict[str, FakeState] = {}

    def set(self, entity_id: str, value: str) -> None:
        self._states[entity_id] = FakeState(str(value))

    def get(self, entity_id: str) -> FakeState | None:
        return self._states.get(entity_id)


class FakeHass:
    """Minimal Home Assistant test double."""

    def __init__(self):
        self.loop = asyncio.get_event_loop()
        self.states = FakeStates()
        self.data: dict = {}
        self.config_entries = SimpleNamespace(_entries={})

    def async_create_task(self, coro):
        return self.loop.create_task(coro)

    async def async_add_executor_job(self, func, *args, **kwargs):
        return func(*args, **kwargs)


class _MemoryStore:
    def __init__(self, initial_data: dict | None = None):
        self.data = initial_data

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = data


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


@pytest.mark.asyncio
async def test_car_permissive_switch_restores_last_on_state(fake_hass, monkeypatch):
    """Permissive switch should restore ON state after restart."""
    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    entry = MockConfigEntry(domain=DOMAIN, title="Planner", data=_base_config(), options={})
    entity = CarPermissiveModeSwitch(coordinator, entry)
    entity.hass = fake_hass

    monkeypatch.setattr(CoordinatorEntity, "async_added_to_hass", AsyncMock())
    monkeypatch.setattr(
        entity,
        "async_get_last_state",
        AsyncMock(return_value=SimpleNamespace(state=STATE_ON)),
    )
    coordinator.async_request_refresh = AsyncMock()

    await entity.async_added_to_hass()

    assert coordinator._car_permissive_mode_active is True
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_car_permissive_switch_skips_restore_when_no_last_state(fake_hass, monkeypatch):
    """Permissive switch should keep default state when nothing was restored."""
    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    entry = MockConfigEntry(domain=DOMAIN, title="Planner", data=_base_config(), options={})
    entity = CarPermissiveModeSwitch(coordinator, entry)
    entity.hass = fake_hass

    monkeypatch.setattr(CoordinatorEntity, "async_added_to_hass", AsyncMock())
    monkeypatch.setattr(entity, "async_get_last_state", AsyncMock(return_value=None))
    coordinator.async_request_refresh = AsyncMock()

    await entity.async_added_to_hass()

    assert coordinator._car_permissive_mode_active is False
    coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_car_permissive_switch_persists_state_before_refresh(fake_hass, monkeypatch):
    """Toggling permissive mode should persist via the coordinator store immediately."""
    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    coordinator._car_permissive_mode_store = _MemoryStore()
    entry = MockConfigEntry(domain=DOMAIN, title="Planner", data=_base_config(), options={})
    entity = CarPermissiveModeSwitch(coordinator, entry)
    coordinator.async_request_refresh = AsyncMock()

    await entity.async_turn_on()

    assert coordinator._car_permissive_mode_active is True
    assert coordinator._car_permissive_mode_store.data["enabled"] is True
    coordinator.async_request_refresh.assert_awaited_once()

    coordinator.async_request_refresh.reset_mock()

    await entity.async_turn_off()

    assert coordinator._car_permissive_mode_active is False
    assert coordinator._car_permissive_mode_store.data["enabled"] is False
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_battery_dump_switch_uses_persistent_override(fake_hass, monkeypatch):
    """Dump switch should delegate persistence to the coordinator override store."""
    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    entry = MockConfigEntry(domain=DOMAIN, title="Planner", data=_base_config(), options={})
    entity = ArbitrageModeSwitch(coordinator, entry)

    coordinator.async_set_battery_dump_mode = AsyncMock()
    coordinator.async_clear_battery_dump_mode = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()

    await entity.async_turn_on()

    coordinator.async_set_battery_dump_mode.assert_awaited_once()
    coordinator.async_request_refresh.assert_awaited_once()

    coordinator.async_request_refresh.reset_mock()

    await entity.async_turn_off()

    coordinator.async_clear_battery_dump_mode.assert_awaited_once()
    coordinator.async_request_refresh.assert_awaited_once()
