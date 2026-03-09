"""Tests for integration setup/reload helpers in __init__.py."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.electricity_planner import (
    _async_migrate_number_entity_ids,
    async_reload_entry,
)
from custom_components.electricity_planner.const import (
    CONF_CURRENT_PRICE_ENTITY,
    CONF_MAX_SOC_THRESHOLD,
    CONF_MAX_SOC_THRESHOLD_SUNNY,
    CONF_SOLAR_FORECAST_START_HOUR,
    CONF_SUNNY_FORECAST_THRESHOLD_KWH,
    DOMAIN,
)


@pytest.mark.asyncio
async def test_async_reload_entry_applies_live_options_without_full_reload():
    """Only live options changed -> apply in-place and skip full reload."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
            CONF_MAX_SOC_THRESHOLD: 90,
            CONF_MAX_SOC_THRESHOLD_SUNNY: 50,
            CONF_SOLAR_FORECAST_START_HOUR: 20,
            CONF_SUNNY_FORECAST_THRESHOLD_KWH: 5.0,
        },
        options={
            CONF_SOLAR_FORECAST_START_HOUR: 18,
            CONF_MAX_SOC_THRESHOLD_SUNNY: 45,
            CONF_SUNNY_FORECAST_THRESHOLD_KWH: 6.5,
        },
    )

    refresh_settings = Mock()
    coordinator = SimpleNamespace(
        config={
            CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
            CONF_MAX_SOC_THRESHOLD: 90,
            CONF_MAX_SOC_THRESHOLD_SUNNY: 50,
            CONF_SOLAR_FORECAST_START_HOUR: 20,
            CONF_SUNNY_FORECAST_THRESHOLD_KWH: 5.0,
        },
        decision_engine=SimpleNamespace(refresh_settings=refresh_settings),
        async_request_refresh=AsyncMock(),
    )

    hass = SimpleNamespace(
        data={DOMAIN: {entry.entry_id: coordinator}},
        config_entries=SimpleNamespace(async_reload=AsyncMock()),
    )

    await async_reload_entry(hass, entry)

    assert coordinator.config[CONF_SOLAR_FORECAST_START_HOUR] == 18
    assert coordinator.config[CONF_MAX_SOC_THRESHOLD_SUNNY] == 45
    assert coordinator.config[CONF_SUNNY_FORECAST_THRESHOLD_KWH] == 6.5
    refresh_settings.assert_called_once_with(coordinator.config)
    coordinator.async_request_refresh.assert_awaited_once()
    hass.config_entries.async_reload.assert_not_called()


@pytest.mark.asyncio
async def test_async_reload_entry_performs_full_reload_for_non_live_changes():
    """Non-live changes should still trigger a full reload."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CURRENT_PRICE_ENTITY: "sensor.current_price_new",
            CONF_MAX_SOC_THRESHOLD: 90,
            CONF_MAX_SOC_THRESHOLD_SUNNY: 50,
            CONF_SOLAR_FORECAST_START_HOUR: 20,
            CONF_SUNNY_FORECAST_THRESHOLD_KWH: 5.0,
        },
        options={},
    )

    coordinator = SimpleNamespace(
        config={
            CONF_CURRENT_PRICE_ENTITY: "sensor.current_price_old",
            CONF_MAX_SOC_THRESHOLD: 90,
            CONF_MAX_SOC_THRESHOLD_SUNNY: 50,
            CONF_SOLAR_FORECAST_START_HOUR: 20,
            CONF_SUNNY_FORECAST_THRESHOLD_KWH: 5.0,
        },
    )

    hass = SimpleNamespace(
        data={DOMAIN: {entry.entry_id: coordinator}},
        config_entries=SimpleNamespace(async_reload=AsyncMock()),
    )

    await async_reload_entry(hass, entry)

    hass.config_entries.async_reload.assert_awaited_once_with(entry.entry_id)


@pytest.mark.asyncio
async def test_async_migrate_number_entity_ids_renames_legacy_number_ids(monkeypatch):
    """Legacy sunny number IDs should be migrated to stable dashboard-friendly IDs."""
    entry = MockConfigEntry(domain=DOMAIN, title="Electricity Planner", data={}, options={})
    unique_id_to_entity_id = {
        f"{entry.entry_id}_max_soc_threshold": "number.electricity_planner_battery_max_soc_threshold",
        f"{entry.entry_id}_max_soc_threshold_sunny": (
            "number.electricity_planner_battery_max_soc_threshold_high_solar"
        ),
        f"{entry.entry_id}_sunny_forecast_threshold_kwh": "number.electricity_planner_sunny_forecast_trigger",
    }

    class FakeRegistry:
        def __init__(self):
            self.calls: list[tuple[str, str]] = []
            self.existing = set(unique_id_to_entity_id.values())

        def async_get_entity_id(self, domain, platform, unique_id):
            assert domain == "number"
            assert platform == DOMAIN
            return unique_id_to_entity_id.get(unique_id)

        def async_get(self, entity_id):
            return object() if entity_id in self.existing else None

        def async_update_entity(self, entity_id, *, new_entity_id):
            self.calls.append((entity_id, new_entity_id))
            self.existing.discard(entity_id)
            self.existing.add(new_entity_id)

    fake_registry = FakeRegistry()
    monkeypatch.setattr(
        "custom_components.electricity_planner.er.async_get",
        lambda hass: fake_registry,
    )

    await _async_migrate_number_entity_ids(SimpleNamespace(), entry)

    assert (
        "number.electricity_planner_battery_max_soc_threshold",
        "number.electricity_planner_max_soc_threshold",
    ) in fake_registry.calls
    assert (
        "number.electricity_planner_battery_max_soc_threshold_high_solar",
        "number.electricity_planner_max_soc_threshold_sunny",
    ) in fake_registry.calls
    assert (
        "number.electricity_planner_sunny_forecast_trigger",
        "number.electricity_planner_sunny_forecast_threshold_kwh",
    ) in fake_registry.calls
