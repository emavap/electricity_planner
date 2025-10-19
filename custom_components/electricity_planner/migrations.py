"""Migration utilities for Electricity Planner configuration."""
from __future__ import annotations

import logging
from typing import Any, Dict

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_BASE_GRID_SETPOINT,
    CONF_USE_DYNAMIC_THRESHOLD,
    CONF_DYNAMIC_THRESHOLD_CONFIDENCE,
    CONF_PRICE_ADJUSTMENT_MULTIPLIER,
    CONF_PRICE_ADJUSTMENT_OFFSET,
    CONF_FEEDIN_ADJUSTMENT_MULTIPLIER,
    CONF_FEEDIN_ADJUSTMENT_OFFSET,
    CONF_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
    CONF_PHASE_MODE,
    CONF_PHASES,
    CONF_BATTERY_PHASE_ASSIGNMENTS,
    DEFAULT_BASE_GRID_SETPOINT,
    DEFAULT_USE_DYNAMIC_THRESHOLD,
    DEFAULT_DYNAMIC_THRESHOLD_CONFIDENCE,
    DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER,
    DEFAULT_PRICE_ADJUSTMENT_OFFSET,
    DEFAULT_FEEDIN_ADJUSTMENT_MULTIPLIER,
    DEFAULT_FEEDIN_ADJUSTMENT_OFFSET,
    DEFAULT_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
    PHASE_MODE_SINGLE,
)

_LOGGER = logging.getLogger(__name__)

# Current config version
CURRENT_VERSION = 10


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate configuration entry to latest version."""
    _LOGGER.info("Migrating configuration from version %s", entry.version)

    if entry.version == 1:
        # Migrate from version 1 to version 2
        new_data = {**entry.data}

        # Add new configuration options introduced in v2
        if CONF_BASE_GRID_SETPOINT not in new_data:
            new_data[CONF_BASE_GRID_SETPOINT] = DEFAULT_BASE_GRID_SETPOINT
            _LOGGER.info("Added base grid setpoint: %sW", DEFAULT_BASE_GRID_SETPOINT)

        # Update entry with new data
        hass.config_entries.async_update_entry(
            entry,
            data=new_data,
            version=2
        )

        _LOGGER.info("Migration to version 2 complete")

    if entry.version == 2:
        # Migrate from version 2 to version 3
        new_data = {**entry.data}

        # Add dynamic threshold configuration options
        if CONF_USE_DYNAMIC_THRESHOLD not in new_data:
            new_data[CONF_USE_DYNAMIC_THRESHOLD] = DEFAULT_USE_DYNAMIC_THRESHOLD
            _LOGGER.info("Added use_dynamic_threshold: %s", DEFAULT_USE_DYNAMIC_THRESHOLD)

        if CONF_DYNAMIC_THRESHOLD_CONFIDENCE not in new_data:
            new_data[CONF_DYNAMIC_THRESHOLD_CONFIDENCE] = DEFAULT_DYNAMIC_THRESHOLD_CONFIDENCE
            _LOGGER.info("Added dynamic_threshold_confidence: %s%%", DEFAULT_DYNAMIC_THRESHOLD_CONFIDENCE)

        # Update entry with new data
        hass.config_entries.async_update_entry(
            entry,
            data=new_data,
            version=3
        )

        _LOGGER.info("Migration to version 3 complete")

    if entry.version == 3:
        # Migrate from version 3 to version 4
        new_data = {**entry.data}

        # Remove deprecated time-based config options (no longer used)
        deprecated_keys = ["emergency_soc_override", "winter_night_soc_override"]
        removed_count = 0
        for key in deprecated_keys:
            if key in new_data:
                del new_data[key]
                removed_count += 1
                _LOGGER.info("Removed deprecated config option: %s", key)

        if removed_count > 0:
            _LOGGER.info("Removed %d deprecated time-based config options", removed_count)

        # Update entry with cleaned data
        hass.config_entries.async_update_entry(
            entry,
            data=new_data,
            version=4
        )

        _LOGGER.info("Migration to version 4 complete")

    if entry.version == 4:
        # Migrate from version 4 to version 5
        new_data = {**entry.data}

        # Remove unused config option that was never used in decision logic
        if "grid_battery_charging_limit_soc" in new_data:
            del new_data["grid_battery_charging_limit_soc"]
            _LOGGER.info("Removed unused config: grid_battery_charging_limit_soc")

        # Update entry with cleaned data
        hass.config_entries.async_update_entry(
            entry,
            data=new_data,
            version=5
        )

        _LOGGER.info("Migration to version 5 complete")

    if entry.version == 5:
        # Migrate from version 5 to version 6
        new_data = {**entry.data}

        if CONF_PRICE_ADJUSTMENT_MULTIPLIER not in new_data:
            new_data[CONF_PRICE_ADJUSTMENT_MULTIPLIER] = DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER

        if CONF_PRICE_ADJUSTMENT_OFFSET not in new_data:
            new_data[CONF_PRICE_ADJUSTMENT_OFFSET] = DEFAULT_PRICE_ADJUSTMENT_OFFSET

        if CONF_FEEDIN_ADJUSTMENT_MULTIPLIER not in new_data:
            new_data[CONF_FEEDIN_ADJUSTMENT_MULTIPLIER] = DEFAULT_FEEDIN_ADJUSTMENT_MULTIPLIER

        if CONF_FEEDIN_ADJUSTMENT_OFFSET not in new_data:
            new_data[CONF_FEEDIN_ADJUSTMENT_OFFSET] = DEFAULT_FEEDIN_ADJUSTMENT_OFFSET

        hass.config_entries.async_update_entry(
            entry,
            data=new_data,
            version=6
        )

        _LOGGER.info("Migration to version 6 complete")

    if entry.version == 6:
        # Migrate from version 6 to version 7
        # No data changes needed - only adds optional nordpool_config_entry field
        # which will be added through UI if user chooses to configure it
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data},
            version=7
        )
        _LOGGER.info("Migration to version 7 complete")

    if entry.version == 7:
        # Migrate from version 7 to version 8
        new_data = {**entry.data}

        # Add car permissive mode multiplier configuration
        if CONF_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER not in new_data:
            new_data[CONF_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER] = DEFAULT_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER
            _LOGGER.info("Added car_permissive_threshold_multiplier: %.1f", DEFAULT_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER)

        hass.config_entries.async_update_entry(
            entry,
            data=new_data,
            version=8
        )

        _LOGGER.info("Migration to version 8 complete")

    if entry.version == 8:
        # Migrate from version 8 to version 9
        new_data = {**entry.data}

        if CONF_PHASE_MODE not in new_data:
            new_data[CONF_PHASE_MODE] = PHASE_MODE_SINGLE
            _LOGGER.info("Set default phase_mode to single-phase")

        new_data.setdefault(CONF_PHASES, {})
        new_data.setdefault(CONF_BATTERY_PHASE_ASSIGNMENTS, {})

        hass.config_entries.async_update_entry(
            entry,
            data=new_data,
            version=9
        )

        _LOGGER.info("Migration to version 9 complete")

    if entry.version == 9:
        # Migrate from version 9 to version 10
        # No data changes needed - version 10 adds optional battery_power_entity per phase
        # which will be added through UI if user chooses to configure it
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data},
            version=10
        )
        _LOGGER.info("Migration to version 10 complete")

    return True


def migrate_config_data(old_data: Dict[str, Any], from_version: int) -> Dict[str, Any]:
    """Migrate configuration data structure (for testing)."""
    new_data = {**old_data}

    if from_version < 2:
        # Add v2 fields if not present
        if CONF_BASE_GRID_SETPOINT not in new_data:
            new_data[CONF_BASE_GRID_SETPOINT] = DEFAULT_BASE_GRID_SETPOINT

    if from_version < 3:
        # Add v3 fields if not present
        if CONF_USE_DYNAMIC_THRESHOLD not in new_data:
            new_data[CONF_USE_DYNAMIC_THRESHOLD] = DEFAULT_USE_DYNAMIC_THRESHOLD

        if CONF_DYNAMIC_THRESHOLD_CONFIDENCE not in new_data:
            new_data[CONF_DYNAMIC_THRESHOLD_CONFIDENCE] = DEFAULT_DYNAMIC_THRESHOLD_CONFIDENCE

    if from_version < 4:
        # Remove deprecated time-based config options
        deprecated_keys = ["emergency_soc_override", "winter_night_soc_override"]
        for key in deprecated_keys:
            new_data.pop(key, None)

    if from_version < 9:
        new_data.setdefault(CONF_PHASE_MODE, PHASE_MODE_SINGLE)
        new_data.setdefault(CONF_PHASES, {})
        new_data.setdefault(CONF_BATTERY_PHASE_ASSIGNMENTS, {})

    if from_version < 10:
        # Version 10 adds optional battery_power_entity per phase - no automatic migration needed
        pass

    if from_version < 5:
        # Remove unused config option
        new_data.pop("grid_battery_charging_limit_soc", None)

    if from_version < 6:
        new_data.setdefault(CONF_PRICE_ADJUSTMENT_MULTIPLIER, DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER)
        new_data.setdefault(CONF_PRICE_ADJUSTMENT_OFFSET, DEFAULT_PRICE_ADJUSTMENT_OFFSET)
        new_data.setdefault(CONF_FEEDIN_ADJUSTMENT_MULTIPLIER, DEFAULT_FEEDIN_ADJUSTMENT_MULTIPLIER)
        new_data.setdefault(CONF_FEEDIN_ADJUSTMENT_OFFSET, DEFAULT_FEEDIN_ADJUSTMENT_OFFSET)

    if from_version < 7:
        # Version 7 adds optional nordpool_config_entry - no automatic migration needed
        pass

    if from_version < 8:
        # Add car permissive mode multiplier
        new_data.setdefault(CONF_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER, DEFAULT_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER)

    return new_data


class ConfigVersionManager:
    """Manage configuration versions and compatibility."""
    
    @staticmethod
    def get_version(config: Dict[str, Any]) -> int:
        """Get configuration version."""
        return config.get("_version", 1)
    
    @staticmethod
    def set_version(config: Dict[str, Any], version: int) -> Dict[str, Any]:
        """Set configuration version."""
        new_config = {**config}
        new_config["_version"] = version
        return new_config
    
    @staticmethod
    def is_compatible(config: Dict[str, Any]) -> bool:
        """Check if configuration is compatible with current version."""
        config_version = ConfigVersionManager.get_version(config)
        return config_version <= CURRENT_VERSION
    
    @staticmethod
    def needs_migration(config: Dict[str, Any]) -> bool:
        """Check if configuration needs migration."""
        config_version = ConfigVersionManager.get_version(config)
        return config_version < CURRENT_VERSION
