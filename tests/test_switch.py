"""Tests for switch platform entities."""
from __future__ import annotations

import asyncio
from datetime import timedelta
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
    BatteryChargingDisableSwitch,
    CarPermissiveModeSwitch,
    NegativeArbitrageBuyModeSwitch,
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
async def test_car_permissive_switch_prefers_coordinator_persisted_state(fake_hass, monkeypatch):
    """Entity restore should not overwrite the coordinator's persisted setting."""
    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    coordinator._car_permissive_mode_active = False
    coordinator._car_permissive_mode_has_persisted_state = True
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

    assert coordinator._car_permissive_mode_active is False
    entity.async_get_last_state.assert_not_awaited()
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
async def test_arbitrage_mode_switch_uses_persistent_override(fake_hass, monkeypatch):
    """Arbitrage switch should delegate persistence to the coordinator override store."""
    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    entry = MockConfigEntry(domain=DOMAIN, title="Planner", data=_base_config(), options={})
    entity = ArbitrageModeSwitch(coordinator, entry)

    coordinator.async_set_arbitrage_mode = AsyncMock()
    coordinator.async_clear_arbitrage_mode = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()

    await entity.async_turn_on()

    coordinator.async_set_arbitrage_mode.assert_awaited_once()
    coordinator.async_request_refresh.assert_awaited_once()

    coordinator.async_request_refresh.reset_mock()

    await entity.async_turn_off()

    coordinator.async_clear_arbitrage_mode.assert_awaited_once()
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_arbitrage_mode_switch_reflects_persisted_override_after_restart(
    fake_hass, monkeypatch,
):
    """Arbitrage switch should prefer the live plan reason after restart refresh."""
    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    coordinator._manual_override_store = _MemoryStore()

    await coordinator.async_set_arbitrage_mode(reason="persisted dump")

    restored = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    restored._manual_override_store = _MemoryStore(coordinator._manual_override_store.data)
    await restored._async_load_manual_overrides()
    restored.data = {
        "arbitrage_mode_plan": {
            "enabled": True,
            "active": False,
            "reason": "Arbitrage mode enabled but no battery data is available",
            "selected_slots_count": 0,
            "selected_slots": [],
            "export_power": 0,
        }
    }

    entry = MockConfigEntry(domain=DOMAIN, title="Planner", data=_base_config(), options={})
    entity = ArbitrageModeSwitch(restored, entry)

    assert entity.is_on is True
    assert entity.extra_state_attributes["override_active"] is True
    assert entity.extra_state_attributes["reason"] == "Arbitrage mode enabled but no battery data is available"


@pytest.mark.asyncio
async def test_arbitrage_mode_switch_uses_override_reason_before_first_refresh(
    fake_hass, monkeypatch,
):
    """Arbitrage switch should fall back to the persisted override reason before plan data exists."""
    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    coordinator._manual_override_store = _MemoryStore()

    await coordinator.async_set_arbitrage_mode(reason="persisted dump")

    restored = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    restored._manual_override_store = _MemoryStore(coordinator._manual_override_store.data)
    await restored._async_load_manual_overrides()

    entry = MockConfigEntry(domain=DOMAIN, title="Planner", data=_base_config(), options={})
    entity = ArbitrageModeSwitch(restored, entry)

    assert entity.is_on is True
    assert entity.extra_state_attributes["reason"] == "persisted dump"


def test_arbitrage_mode_switch_ignores_stale_plan_when_override_is_cleared(fake_hass, monkeypatch):
    """Stale arbitrage-plan data must not leak through after arbitrage mode is turned off."""
    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    coordinator.data = {
        "arbitrage_mode_plan": {
            "enabled": True,
            "active": True,
            "reason": "Arbitrage export active at 0.120€/kWh",
            "arbitrage_price_threshold": 0.08,
            "current_slot_price": 0.12,
            "selected_slots_count": 3,
            "selected_slots": [{"start": "x", "end": "y", "price": 0.12}],
            "deadline": "2026-04-20T21:00:00+02:00",
            "export_power": 3000,
            "configured_export_cap_w": 3000,
            "available_energy_kwh": 5.0,
        }
    }
    coordinator._manual_overrides["arbitrage_mode"] = None

    entry = MockConfigEntry(domain=DOMAIN, title="Planner", data=_base_config(), options={})
    entity = ArbitrageModeSwitch(coordinator, entry)
    attrs = entity.extra_state_attributes

    assert entity.is_on is False
    assert attrs["override_active"] is False
    assert attrs["reason"] == "Arbitrage mode disabled"
    assert attrs["currently_exporting"] is False
    assert attrs["arbitrage_price_threshold"] is None
    assert attrs["selected_slots_count"] == 0
    assert attrs["selected_slots"] == []
    assert attrs["export_power"] is None


def test_battery_disable_switch_ignores_expired_override(fake_hass, monkeypatch):
    """Expired battery overrides should not keep the disable switch ON."""
    base_time = coordinator_module.dt_util.utcnow()
    monkeypatch.setattr(
        coordinator_module.dt_util,
        "utcnow",
        lambda: base_time,
        raising=False,
    )

    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    coordinator._manual_overrides["battery_grid_charging"] = {
        "value": False,
        "reason": "temporary disable",
        "expires_at": base_time - timedelta(minutes=1),
        "set_at": base_time - timedelta(minutes=5),
    }

    entry = MockConfigEntry(domain=DOMAIN, title="Planner", data=_base_config(), options={})
    entity = BatteryChargingDisableSwitch(coordinator, entry)

    assert entity.is_on is False
    assert coordinator._manual_overrides["battery_grid_charging"] is None



@pytest.mark.asyncio
async def test_negative_buy_switch_uses_persistent_override(fake_hass, monkeypatch):
    """Negative-buy switch should delegate persistence to the coordinator override store."""
    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    entry = MockConfigEntry(domain=DOMAIN, title="Planner", data=_base_config(), options={})
    entity = NegativeArbitrageBuyModeSwitch(coordinator, entry)

    coordinator.async_set_negative_buy_mode = AsyncMock()
    coordinator.async_clear_negative_buy_mode = AsyncMock()
    coordinator.async_request_refresh = AsyncMock()

    await entity.async_turn_on()

    coordinator.async_set_negative_buy_mode.assert_awaited_once()
    coordinator.async_request_refresh.assert_awaited_once()

    coordinator.async_request_refresh.reset_mock()

    await entity.async_turn_off()

    coordinator.async_clear_negative_buy_mode.assert_awaited_once()
    coordinator.async_request_refresh.assert_awaited_once()


def test_negative_buy_switch_reports_attributes_from_active_plan(fake_hass, monkeypatch):
    """When armed, the switch should expose plan threshold, slots, and active flags."""
    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    base_time = coordinator_module.dt_util.utcnow()
    coordinator._manual_overrides["negative_buy_mode"] = {
        "value": True,
        "reason": "Manual Negative Arbitrage Buy",
        "expires_at": None,
        "set_at": base_time,
    }
    coordinator.data = {
        "negative_buy_plan": {
            "enabled": True,
            "active": True,
            "solar_curtail_active": True,
            "reason": "Negative Arbitrage Buy active at -0.120€/kWh",
            "threshold": -0.05,
            "deadline": "2025-10-14T10:00:00+02:00",
            "required_energy_kwh": 4.0,
            "required_duration_hours": 1.0,
            "slots_cover_full_charge": True,
            "buy_price_threshold": -0.10,
            "current_slot_price": -0.12,
            "selected_slots": [{"start": "x", "end": "y", "price": -0.12}],
            "selected_slots_count": 4,
            "import_power": 4000,
            "configured_import_cap_w": 4000,
        }
    }

    entry = MockConfigEntry(domain=DOMAIN, title="Planner", data=_base_config(), options={})
    entity = NegativeArbitrageBuyModeSwitch(coordinator, entry)
    attrs = entity.extra_state_attributes

    assert entity.is_on is True
    assert attrs["override_active"] is True
    assert attrs["currently_buying"] is True
    assert attrs["buy_price_threshold"] == -0.10
    assert attrs["current_slot_price"] == -0.12
    assert attrs["selected_slots_count"] == 4
    assert attrs["import_power"] == 4000
    assert attrs["configured_import_cap_w"] == 4000
    assert "Negative Arbitrage Buy active" in attrs["reason"]


def test_negative_buy_switch_ignores_stale_plan_when_override_is_cleared(fake_hass, monkeypatch):
    """Stale negative-buy plan data must not leak through after the mode is turned off."""
    coordinator = _create_coordinator(fake_hass, _base_config(), monkeypatch)
    coordinator.data = {
        "negative_buy_plan": {
            "enabled": True,
            "active": True,
            "reason": "Negative Arbitrage Buy active at -0.120€/kWh",
            "buy_price_threshold": -0.10,
            "current_slot_price": -0.12,
            "selected_slots": [{"start": "x", "end": "y", "price": -0.12}],
            "selected_slots_count": 4,
            "import_power": 4000,
        }
    }
    coordinator._manual_overrides["negative_buy_mode"] = None

    entry = MockConfigEntry(domain=DOMAIN, title="Planner", data=_base_config(), options={})
    entity = NegativeArbitrageBuyModeSwitch(coordinator, entry)
    attrs = entity.extra_state_attributes

    assert entity.is_on is False
    assert attrs["override_active"] is False
    assert attrs["reason"] == "Negative Arbitrage Buy mode disabled"
    assert attrs["currently_buying"] is False
    assert attrs["buy_price_threshold"] is None
    assert attrs["selected_slots_count"] == 0
    assert attrs["selected_slots"] == []
