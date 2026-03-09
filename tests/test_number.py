"""Tests for number platform entities."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from datetime import datetime

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import pytz
from homeassistant.util import dt as dt_util

from custom_components.electricity_planner import coordinator as coordinator_module
from custom_components.electricity_planner.const import (
    CONF_BATTERY_SOC_ENTITIES,
    CONF_CURRENT_PRICE_ENTITY,
    CONF_HIGHEST_PRICE_ENTITY,
    CONF_LOWEST_PRICE_ENTITY,
    CONF_NEXT_PRICE_ENTITY,
    CONF_SOLAR_FORECAST_ENTITY_TOMORROW,
    CONF_SOLAR_FORECAST_START_HOUR,
    CONF_SOLAR_FORECAST_TODAY_ENTITY,
    DOMAIN,
)
from custom_components.electricity_planner.coordinator import ElectricityPlannerCoordinator
from custom_components.electricity_planner.number import (
    MaxSocThresholdNumber,
    MaxSocThresholdSunnyNumber,
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
        self.config_entries = SimpleNamespace(_entries={})

    def async_create_task(self, coro):
        return self.loop.create_task(coro)

    async def async_add_executor_job(self, func, *args, **kwargs):
        return func(*args, **kwargs)


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
async def test_number_setup_always_adds_standard_and_sunny_entities(fake_hass, monkeypatch):
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

    assert len(entities) == 3
    assert isinstance(entities[0], MaxSocThresholdNumber)
    assert isinstance(entities[1], MaxSocThresholdSunnyNumber)
    assert isinstance(entities[2], SunnyForecastThresholdNumber)


@pytest.mark.asyncio
async def test_number_setup_with_forecast_entity_keeps_same_three_entities(fake_hass, monkeypatch):
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

    assert len(entities) == 3
    assert isinstance(entities[0], MaxSocThresholdNumber)
    assert isinstance(entities[1], MaxSocThresholdSunnyNumber)
    assert isinstance(entities[2], SunnyForecastThresholdNumber)


@pytest.mark.asyncio
async def test_number_entities_use_stable_dashboard_friendly_entity_ids(fake_hass, monkeypatch):
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

    assert max_entity.entity_id == "number.electricity_planner_max_soc_threshold"
    assert sunny_entity.entity_id == "number.electricity_planner_max_soc_threshold_sunny"
    assert (
        forecast_entity.entity_id
        == "number.electricity_planner_sunny_forecast_threshold_kwh"
    )


@pytest.mark.asyncio
async def test_sunny_number_attributes_use_live_forecast_fallback_before_start_hour(fake_hass, monkeypatch):
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
