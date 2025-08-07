"""The Electricity Planner integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import (
    DOMAIN,
    DEFAULT_EMERGENCY_SOC,
    DEFAULT_VERY_LOW_PRICE_THRESHOLD,
    DEFAULT_SIGNIFICANT_SOLAR_THRESHOLD,
    DEFAULT_POOR_SOLAR_FORECAST,
    DEFAULT_EXCELLENT_SOLAR_FORECAST,
)
from .coordinator import ElectricityPlannerCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old entry."""
    _LOGGER.debug("Migrating from version %s", entry.version)
    
    if entry.version == 1:
        # Migration from version 1 to version 2: add new threshold defaults
        new_data = dict(entry.data)
        
        # Add new configurable thresholds with defaults if not present
        if "emergency_soc_threshold" not in new_data:
            new_data["emergency_soc_threshold"] = DEFAULT_EMERGENCY_SOC
        if "very_low_price_threshold" not in new_data:
            new_data["very_low_price_threshold"] = DEFAULT_VERY_LOW_PRICE_THRESHOLD
        if "significant_solar_threshold" not in new_data:
            new_data["significant_solar_threshold"] = DEFAULT_SIGNIFICANT_SOLAR_THRESHOLD
        if "poor_solar_forecast_threshold" not in new_data:
            new_data["poor_solar_forecast_threshold"] = DEFAULT_POOR_SOLAR_FORECAST
        if "excellent_solar_forecast_threshold" not in new_data:
            new_data["excellent_solar_forecast_threshold"] = DEFAULT_EXCELLENT_SOLAR_FORECAST
        
        hass.config_entries.async_update_entry(entry, data=new_data, version=2)
        _LOGGER.info("Migration to version 2 successful")
    
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Electricity Planner from a config entry."""
    coordinator = ElectricityPlannerCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok