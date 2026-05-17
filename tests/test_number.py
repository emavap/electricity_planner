"""Tests for number platform entities."""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pytz
from homeassistant.components.number import async_set_value as async_number_set_value
from homeassistant.core import ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.electricity_planner import coordinator as coordinator_module
from custom_components.electricity_planner.const import (
    CONF_ARBITRAGE_MODE_RESERVE_SOC,
    CONF_BATTERY_SOC_ENTITIES,
    CONF_CURRENT_PRICE_ENTITY,
    CONF_HIGHEST_PRICE_ENTITY,
    CONF_LOWEST_PRICE_ENTITY,
    CONF_MAX_SOC_THRESHOLD,
    CONF_MAX_SOC_THRESHOLD_SUNNY,
    CONF_NEXT_PRICE_ENTITY,
    CONF_SOLAR_FORECAST_ENTITY_TOMORROW,
    CONF_SOLAR_FORECAST_START_HOUR,
    CONF_SOLAR_FORECAST_TODAY_ENTITY,
    CONF_SUNNY_FORECAST_THRESHOLD_KWH,
    DOMAIN,
)
from custom_components.electricity_planner.coordinator import (
    ElectricityPlannerCoordinator,
)
from custom_components.electricity_planner.number import (
    ArbitrageModeDeadlineHourNumber,
    ArbitrageModeReserveSocNumber,
    MaxSocThresholdNumber,
    MaxSocThresholdSolarNumber,
    MaxSocThresholdSunnyNumber,
    NegativeBuyThresholdNumber,
    SunnyForecastThresholdNumber,
    async_setup_entry,
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


class FakeServices:
    """Minimal fake service registry."""

    async def async_call(self, domain, service, data, blocking=False, context=None):
        return None


class FakeHass:
    """Minimal Home Assistant test double."""

    def __init__(self):
        self.loop = asyncio.get_event_loop()
        self.states = FakeStates()
        self.services = FakeServices()
        self.data: dict = {}
        self.config_entries = SimpleNamespace(
            _entries={},
            async_get_entry=self._async_get_entry,
            async_update_entry=self._async_update_entry,
        )

    def async_create_task(self, coro):
        return self.loop.create_task(coro)

    async def async_add_executor_job(self, func, *args, **kwargs):
        return func(*args, **kwargs)

    def _async_get_entry(self, entry_id):
        return self.config_entries._entries.get(entry_id)

    def _async_update_entry(self, entry, *, data=None, options=None, version=None):
        if data is not None:
            object.__setattr__(entry, "data", data)
        if options is not None:
            object.__setattr__(entry, "options", options)
        if version is not None:
            object.__setattr__(entry, "version", version)
        self.config_entries._entries[entry.entry_id] = entry


@pytest.fixture
def fake_hass():
    return FakeHass()


def _base_config() -> dict[str, object]:
    return {
        CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
        CONF_HIGHEST_PRICE_ENTITY: "sensor.highest_price",
        CONF_LOWEST_PRICE_ENTITY: "sensor.lowest_price",
        CONF_NEXT_PRICE_ENTITY: "sensor.next_price",
        CONF_BATTERY_SOC_ENTITIES: ["sensor.battery_soc_1"],
    }


@pytest.mark.asyncio
async def test_number_setup_always_adds_standard_and_sunny_entities(
    fake_hass, monkeypatch
):
    """Sunny SOC and sunny forecast threshold numbers should always be exposed."""
    config = _base_config()
    entry = MockConfigEntry(domain=DOMAIN, data=config, options={})

    monkeypatch.setattr(
        coordinator_module.ElectricityPlannerCoordinator,
        "_setup_entity_listeners",
        lambda self: None,
    )

    coordinator = ElectricityPlannerCoordinator(fake_hass, entry)
    fake_hass.data = {DOMAIN: {entry.entry_id: coordinator}}

    entities = []
    await async_setup_entry(fake_hass, entry, entities.extend)

    assert len(entities) == 7
    assert isinstance(entities[0], MaxSocThresholdNumber)
    assert isinstance(entities[1], MaxSocThresholdSunnyNumber)
    assert isinstance(entities[2], MaxSocThresholdSolarNumber)
    assert isinstance(entities[3], SunnyForecastThresholdNumber)
    assert isinstance(entities[4], ArbitrageModeReserveSocNumber)
    assert isinstance(entities[5], ArbitrageModeDeadlineHourNumber)
    assert isinstance(entities[6], NegativeBuyThresholdNumber)


@pytest.mark.asyncio
async def test_number_setup_with_forecast_entity_keeps_same_three_entities(
    fake_hass, monkeypatch
):
    """Providing forecast entity should not change number entity count."""
    config = _base_config()
    config[CONF_SOLAR_FORECAST_ENTITY_TOMORROW] = "sensor.energy_production_tomorrow"
    entry = MockConfigEntry(domain=DOMAIN, data=config, options={})

    monkeypatch.setattr(
        coordinator_module.ElectricityPlannerCoordinator,
        "_setup_entity_listeners",
        lambda self: None,
    )

    coordinator = ElectricityPlannerCoordinator(fake_hass, entry)
    fake_hass.data = {DOMAIN: {entry.entry_id: coordinator}}

    entities = []
    await async_setup_entry(fake_hass, entry, entities.extend)

    assert len(entities) == 7
    assert isinstance(entities[0], MaxSocThresholdNumber)
    assert isinstance(entities[1], MaxSocThresholdSunnyNumber)
    assert isinstance(entities[2], MaxSocThresholdSolarNumber)
    assert isinstance(entities[3], SunnyForecastThresholdNumber)
    assert isinstance(entities[4], ArbitrageModeReserveSocNumber)
    assert isinstance(entities[5], ArbitrageModeDeadlineHourNumber)
    assert isinstance(entities[6], NegativeBuyThresholdNumber)


@pytest.mark.asyncio
async def test_number_entities_use_stable_dashboard_friendly_entity_ids(
    fake_hass, monkeypatch
):
    """Number entities should expose stable IDs expected by dashboard templates."""
    config = _base_config()
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Electricity Planner",
        data=config,
        options={},
    )

    monkeypatch.setattr(
        coordinator_module.ElectricityPlannerCoordinator,
        "_setup_entity_listeners",
        lambda self: None,
    )

    coordinator = ElectricityPlannerCoordinator(fake_hass, entry)

    max_entity = MaxSocThresholdNumber(coordinator, entry)
    sunny_entity = MaxSocThresholdSunnyNumber(coordinator, entry)
    forecast_entity = SunnyForecastThresholdNumber(coordinator, entry)
    reserve_entity = ArbitrageModeReserveSocNumber(coordinator, entry)
    assert max_entity.entity_id == "number.electricity_planner_max_soc_threshold"
    assert (
        sunny_entity.entity_id == "number.electricity_planner_max_soc_threshold_sunny"
    )
    assert (
        forecast_entity.entity_id
        == "number.electricity_planner_sunny_forecast_threshold_kwh"
    )
    assert (
        reserve_entity.entity_id
        == "number.electricity_planner_arbitrage_mode_reserve_soc"
    )


@pytest.mark.asyncio
async def test_sunny_number_attributes_use_live_forecast_fallback_before_start_hour(
    fake_hass, monkeypatch
):
    """When coordinator data is empty, UI attrs should still show today's forecast value."""
    config = _base_config()
    config[CONF_SOLAR_FORECAST_ENTITY_TOMORROW] = "sensor.energy_production_tomorrow"
    config[CONF_SOLAR_FORECAST_TODAY_ENTITY] = "sensor.energy_production_today"
    config[CONF_SOLAR_FORECAST_START_HOUR] = 20
    entry = MockConfigEntry(domain=DOMAIN, data=config, options={})

    monkeypatch.setattr(
        coordinator_module.ElectricityPlannerCoordinator,
        "_setup_entity_listeners",
        lambda self: None,
    )

    tz = pytz.timezone("Europe/Brussels")
    monkeypatch.setattr(dt_util, "now", lambda: datetime(2025, 6, 16, 19, 0, tzinfo=tz))

    fake_hass.states.set("sensor.energy_production_today", "13.239")
    coordinator = ElectricityPlannerCoordinator(fake_hass, entry)
    coordinator.data = None

    entity = MaxSocThresholdSunnyNumber(coordinator, entry)
    attrs = entity.extra_state_attributes

    assert attrs.get("solar_forecast_kwh") == "13.2 kWh"


@pytest.mark.asyncio
async def test_max_soc_number_update_preserves_other_options(fake_hass, monkeypatch):
    """Changing one number should keep other option values intact."""
    config = _base_config()
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=config,
        options={
            CONF_MAX_SOC_THRESHOLD_SUNNY: 35,
            CONF_SUNNY_FORECAST_THRESHOLD_KWH: 5.0,
        },
    )

    monkeypatch.setattr(
        coordinator_module.ElectricityPlannerCoordinator,
        "_setup_entity_listeners",
        lambda self: None,
    )

    fake_hass.config_entries._entries[entry.entry_id] = entry
    coordinator = ElectricityPlannerCoordinator(fake_hass, entry)
    coordinator.decision_engine.refresh_settings = Mock()
    coordinator.async_request_refresh = AsyncMock()

    entity = MaxSocThresholdNumber(coordinator, entry)
    entity.hass = fake_hass

    await entity.async_set_native_value(80)

    assert entry.options[CONF_MAX_SOC_THRESHOLD] == 80
    assert entry.options[CONF_MAX_SOC_THRESHOLD_SUNNY] == 35
    assert entry.options[CONF_SUNNY_FORECAST_THRESHOLD_KWH] == 5.0
    assert coordinator.config[CONF_MAX_SOC_THRESHOLD] == 80
    coordinator.decision_engine.refresh_settings.assert_called_once_with(
        coordinator.config
    )
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_arbitrage_mode_reserve_update_persists_in_options(
    fake_hass, monkeypatch
):
    """Reserve SOC should persist like the other live number controls."""
    config = _base_config()
    entry = MockConfigEntry(domain=DOMAIN, data=config, options={})

    monkeypatch.setattr(
        coordinator_module.ElectricityPlannerCoordinator,
        "_setup_entity_listeners",
        lambda self: None,
    )

    fake_hass.config_entries._entries[entry.entry_id] = entry
    coordinator = ElectricityPlannerCoordinator(fake_hass, entry)
    coordinator.decision_engine.refresh_settings = Mock()
    coordinator.async_request_refresh = AsyncMock()

    entity = ArbitrageModeReserveSocNumber(coordinator, entry)
    entity.hass = fake_hass

    await entity.async_set_native_value(25)

    assert entry.options[CONF_ARBITRAGE_MODE_RESERVE_SOC] == 25
    assert coordinator.config[CONF_ARBITRAGE_MODE_RESERVE_SOC] == 25
    coordinator.decision_engine.refresh_settings.assert_called_once_with(
        coordinator.config
    )
    coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_arbitrage_mode_reserve_attributes_show_threshold_state_not_windows(
    fake_hass, monkeypatch
):
    """Reserve number attributes should reflect threshold-based planning state."""
    config = _base_config()
    entry = MockConfigEntry(domain=DOMAIN, data=config, options={})

    monkeypatch.setattr(
        coordinator_module.ElectricityPlannerCoordinator,
        "_setup_entity_listeners",
        lambda self: None,
    )

    coordinator = ElectricityPlannerCoordinator(fake_hass, entry)
    coordinator.data = {
        "battery_analysis": {"average_soc": 54.2},
        "arbitrage_mode_plan": {
            "enabled": True,
            "arbitrage_price_threshold": 0.1123,
            "current_slot_price": 0.1456,
            "selected_slots_count": 5,
            "slots_cover_full_arbitrage": True,
        },
    }

    entity = ArbitrageModeReserveSocNumber(coordinator, entry)
    attrs = entity.extra_state_attributes

    assert attrs["arbitrage_mode_enabled"] is True
    assert attrs["current_battery_soc"] == "54.2%"
    assert attrs["available_to_export_percent"] == "14.2%"
    assert attrs["arbitrage_price_threshold"] == 0.1123
    assert attrs["current_slot_price"] == 0.1456
    assert attrs["selected_slots_count"] == 5
    assert attrs["slots_cover_full_arbitrage"] is True
    assert "scheduled_window_start" not in attrs
    assert "scheduled_window_end" not in attrs


@pytest.mark.asyncio
async def test_sunny_max_soc_update_preserves_live_max_soc_from_coordinator(
    fake_hass, monkeypatch
):
    """Saving one live number should not drop sibling live values from the active config."""
    config = _base_config()
    config[CONF_MAX_SOC_THRESHOLD] = 90
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=config,
        options={
            CONF_SUNNY_FORECAST_THRESHOLD_KWH: 5.0,
        },
    )

    monkeypatch.setattr(
        coordinator_module.ElectricityPlannerCoordinator,
        "_setup_entity_listeners",
        lambda self: None,
    )

    fake_hass.config_entries._entries[entry.entry_id] = entry
    coordinator = ElectricityPlannerCoordinator(fake_hass, entry)
    coordinator.config[CONF_MAX_SOC_THRESHOLD] = 80
    coordinator.decision_engine.refresh_settings = Mock()
    coordinator.async_request_refresh = AsyncMock()

    entity = MaxSocThresholdSunnyNumber(coordinator, entry)
    entity.hass = fake_hass

    await entity.async_set_native_value(35)

    assert entry.options[CONF_MAX_SOC_THRESHOLD] == 80
    assert entry.options[CONF_MAX_SOC_THRESHOLD_SUNNY] == 35
    assert entry.options[CONF_SUNNY_FORECAST_THRESHOLD_KWH] == 5.0


@pytest.mark.asyncio
async def test_max_soc_update_rejects_value_below_existing_sunny_threshold(
    fake_hass, monkeypatch
):
    """Normal max SOC cannot be lowered below the active sunny threshold."""
    config = _base_config()
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=config,
        options={
            CONF_MAX_SOC_THRESHOLD: 70,
            CONF_MAX_SOC_THRESHOLD_SUNNY: 35,
        },
    )

    monkeypatch.setattr(
        coordinator_module.ElectricityPlannerCoordinator,
        "_setup_entity_listeners",
        lambda self: None,
    )

    fake_hass.config_entries._entries[entry.entry_id] = entry
    coordinator = ElectricityPlannerCoordinator(fake_hass, entry)
    coordinator.decision_engine.refresh_settings = Mock()
    coordinator.async_request_refresh = AsyncMock()

    entity = MaxSocThresholdNumber(coordinator, entry)
    entity.hass = fake_hass

    with pytest.raises(HomeAssistantError):
        await entity.async_set_native_value(30)

    assert entry.options[CONF_MAX_SOC_THRESHOLD] == 70
    assert entry.options[CONF_MAX_SOC_THRESHOLD_SUNNY] == 35
    coordinator.decision_engine.refresh_settings.assert_not_called()
    coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_sunny_max_soc_update_rejects_value_above_normal_threshold(
    fake_hass, monkeypatch
):
    """Sunny max SOC cannot be raised above the active normal threshold."""
    config = _base_config()
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=config,
        options={
            CONF_MAX_SOC_THRESHOLD: 70,
            CONF_MAX_SOC_THRESHOLD_SUNNY: 35,
        },
    )

    monkeypatch.setattr(
        coordinator_module.ElectricityPlannerCoordinator,
        "_setup_entity_listeners",
        lambda self: None,
    )

    fake_hass.config_entries._entries[entry.entry_id] = entry
    coordinator = ElectricityPlannerCoordinator(fake_hass, entry)
    coordinator.decision_engine.refresh_settings = Mock()
    coordinator.async_request_refresh = AsyncMock()

    entity = MaxSocThresholdSunnyNumber(coordinator, entry)
    entity.hass = fake_hass

    with pytest.raises(HomeAssistantError):
        await entity.async_set_native_value(75)

    assert entry.options[CONF_MAX_SOC_THRESHOLD] == 70
    assert entry.options[CONF_MAX_SOC_THRESHOLD_SUNNY] == 35
    coordinator.decision_engine.refresh_settings.assert_not_called()
    coordinator.async_request_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_number_service_wrapper_rejects_invalid_sunny_threshold(
    fake_hass, monkeypatch
):
    """Home Assistant's number service wrapper should propagate the sunny/max invariant."""
    config = _base_config()
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Electricity Planner",
        data=config,
        options={
            CONF_MAX_SOC_THRESHOLD: 70,
            CONF_MAX_SOC_THRESHOLD_SUNNY: 35,
        },
    )
    monkeypatch.setattr(
        coordinator_module.ElectricityPlannerCoordinator,
        "_setup_entity_listeners",
        lambda self: None,
    )

    fake_hass.config_entries._entries[entry.entry_id] = entry
    coordinator = ElectricityPlannerCoordinator(fake_hass, entry)
    coordinator.decision_engine.refresh_settings = Mock()
    coordinator.async_request_refresh = AsyncMock()
    entity = MaxSocThresholdSunnyNumber(coordinator, entry)
    entity.hass = fake_hass

    with pytest.raises(HomeAssistantError):
        await async_number_set_value(
            entity,
            ServiceCall(
                fake_hass,
                "number",
                "set_value",
                {"value": 75},
            ),
        )

    assert entry.options[CONF_MAX_SOC_THRESHOLD] == 70
    assert entry.options[CONF_MAX_SOC_THRESHOLD_SUNNY] == 35
