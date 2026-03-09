"""Tests for integration setup/reload helpers in __init__.py."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.electricity_planner import async_reload_entry
from custom_components.electricity_planner.const import (
    CONF_CURRENT_PRICE_ENTITY,
    CONF_MAX_SOC_THRESHOLD,
    CONF_MAX_SOC_THRESHOLD_SUNNY,
    CONF_SOLAR_FORECAST_START_HOUR,
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
        },
        options={
            CONF_SOLAR_FORECAST_START_HOUR: 18,
            CONF_MAX_SOC_THRESHOLD_SUNNY: 45,
        },
    )

    refresh_settings = Mock()
    coordinator = SimpleNamespace(
        config={
            CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
            CONF_MAX_SOC_THRESHOLD: 90,
            CONF_MAX_SOC_THRESHOLD_SUNNY: 50,
            CONF_SOLAR_FORECAST_START_HOUR: 20,
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
        },
        options={},
    )

    coordinator = SimpleNamespace(
        config={
            CONF_CURRENT_PRICE_ENTITY: "sensor.current_price_old",
            CONF_MAX_SOC_THRESHOLD: 90,
            CONF_MAX_SOC_THRESHOLD_SUNNY: 50,
            CONF_SOLAR_FORECAST_START_HOUR: 20,
        },
    )

    hass = SimpleNamespace(
        data={DOMAIN: {entry.entry_id: coordinator}},
        config_entries=SimpleNamespace(async_reload=AsyncMock()),
    )

    await async_reload_entry(hass, entry)

    hass.config_entries.async_reload.assert_awaited_once_with(entry.entry_id)
