"""Migration utilities for Electricity Planner configuration.

Schema Version History
----------------------
v1  → v2:  Added ``base_grid_setpoint`` option.
v2  → v3:  Added ``use_dynamic_threshold`` and ``dynamic_threshold_confidence``.
v3  → v4:  Removed deprecated ``emergency_soc_override`` / ``winter_night_soc_override``.
v4  → v5:  Removed unused ``grid_battery_charging_limit_soc``.
v5  → v6:  Added price/feed-in adjustment multipliers and offsets.
v6  → v7:  Added optional ``nordpool_config_entry`` field (no data migration needed).
v7  → v8:  Added ``car_permissive_threshold_multiplier``.
v8  → v9:  Added ``phase_mode``, ``phases``, and ``battery_phase_assignments`` for
           three-phase support.
v9  → v10: Added optional ``battery_power_entity`` per phase (no data migration needed).
v10 → v11: Added ``soc_price_multiplier_max`` and ``soc_buffer_target`` for
           SOC-based dynamic price threshold adjustments.
v11 → v12: Added ``solar_forecast_entity`` and ``max_soc_threshold_sunny`` for
           sunny-day grid charging limits.
v13 → v14: Added ``sunny_forecast_threshold_kwh`` trigger for sunny-day mode.
"""
from __future__ import annotations

import logging
from typing import Any

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
    CONF_SOC_PRICE_MULTIPLIER_MAX,
    CONF_SOC_BUFFER_TARGET,
    CONF_MAX_SOC_THRESHOLD_SUNNY,
    CONF_SOLAR_FORECAST_START_HOUR,
    CONF_BATTERY_CAPACITIES,
    CONF_SUNNY_FORECAST_THRESHOLD_KWH,
    DEFAULT_BASE_GRID_SETPOINT,
    DEFAULT_USE_DYNAMIC_THRESHOLD,
    DEFAULT_DYNAMIC_THRESHOLD_CONFIDENCE,
    DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER,
    DEFAULT_PRICE_ADJUSTMENT_OFFSET,
    DEFAULT_FEEDIN_ADJUSTMENT_MULTIPLIER,
    DEFAULT_FEEDIN_ADJUSTMENT_OFFSET,
    DEFAULT_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
    DEFAULT_SOC_PRICE_MULTIPLIER_MAX,
    DEFAULT_SOC_BUFFER_TARGET,
    DEFAULT_MAX_SOC_SUNNY,
    DEFAULT_SOLAR_FORECAST_START_HOUR,
    DEFAULT_SUNNY_FORECAST_THRESHOLD_KWH,
    PHASE_MODE_SINGLE,
)

_LOGGER = logging.getLogger(__name__)

# Current config version
CURRENT_VERSION = 14


def _validate_numeric_config(
    config: dict[str, Any],
    key: str,
    min_val: float,
    max_val: float,
    default: float,
    name: str
) -> None:
    """Validate and clamp numeric configuration values."""
    if key in config:
        try:
            value = float(config[key])
            if not min_val <= value <= max_val:
                _LOGGER.warning(
                    "Migration: %s value %.2f out of range [%.2f, %.2f], resetting to default %.2f",
                    name, value, min_val, max_val, default
                )
                config[key] = default
        except (TypeError, ValueError):
            _LOGGER.warning(
                "Migration: %s value %s invalid, resetting to default %.2f",
                name, config[key], default
            )
            config[key] = default


def _derive_sunny_forecast_threshold_kwh(config: dict[str, Any]) -> float:
    """Derive kWh trigger from configured battery capacities."""
    capacities = config.get(CONF_BATTERY_CAPACITIES, {}) or {}
    total_capacity = 0.0
    for raw_value in capacities.values():
        try:
            capacity = float(raw_value)
        except (TypeError, ValueError):
            continue
        if capacity > 0:
            total_capacity += capacity

    if total_capacity > 0:
        return round(total_capacity / 2.0, 1)
    return DEFAULT_SUNNY_FORECAST_THRESHOLD_KWH


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

        # Validate the new value
        _validate_numeric_config(
            new_data, CONF_BASE_GRID_SETPOINT, 1000, 15000,
            DEFAULT_BASE_GRID_SETPOINT, "base_grid_setpoint"
        )

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

    if entry.version == 10:
        # Migrate from version 10 to version 11
        new_data = {**entry.data}

        # Add SOC-based price multiplier settings
        if CONF_SOC_PRICE_MULTIPLIER_MAX not in new_data:
            new_data[CONF_SOC_PRICE_MULTIPLIER_MAX] = DEFAULT_SOC_PRICE_MULTIPLIER_MAX
            _LOGGER.info("Added soc_price_multiplier_max: %.2f", DEFAULT_SOC_PRICE_MULTIPLIER_MAX)

        if CONF_SOC_BUFFER_TARGET not in new_data:
            new_data[CONF_SOC_BUFFER_TARGET] = DEFAULT_SOC_BUFFER_TARGET
            _LOGGER.info("Added soc_buffer_target: %d%%", DEFAULT_SOC_BUFFER_TARGET)

        hass.config_entries.async_update_entry(
            entry,
            data=new_data,
            version=11
        )

        _LOGGER.info("Migration to version 11 complete")

    if entry.version == 11:
        # Migrate from version 11 to version 12
        new_data = {**entry.data}

        # Add sunny-day grid charging limit
        if CONF_MAX_SOC_THRESHOLD_SUNNY not in new_data:
            new_data[CONF_MAX_SOC_THRESHOLD_SUNNY] = DEFAULT_MAX_SOC_SUNNY
            _LOGGER.info("Added max_soc_threshold_sunny: %d%%", DEFAULT_MAX_SOC_SUNNY)

        # solar_forecast_entity is optional, no default needed

        hass.config_entries.async_update_entry(
            entry,
            data=new_data,
            version=12
        )

        _LOGGER.info("Migration to version 12 complete")

    if entry.version == 12:
        # Migrate from version 12 to version 13
        new_data = {**entry.data}

        # Add configurable solar forecast start hour
        if CONF_SOLAR_FORECAST_START_HOUR not in new_data:
            new_data[CONF_SOLAR_FORECAST_START_HOUR] = DEFAULT_SOLAR_FORECAST_START_HOUR
            _LOGGER.info("Added solar_forecast_start_hour: %d", DEFAULT_SOLAR_FORECAST_START_HOUR)

        hass.config_entries.async_update_entry(
            entry,
            data=new_data,
            version=13
        )

        _LOGGER.info("Migration to version 13 complete")

    if entry.version == 13:
        # Migrate from version 13 to version 14
        new_data = {**entry.data}

        if CONF_SUNNY_FORECAST_THRESHOLD_KWH not in new_data:
            derived_threshold = _derive_sunny_forecast_threshold_kwh(new_data)
            new_data[CONF_SUNNY_FORECAST_THRESHOLD_KWH] = derived_threshold
            _LOGGER.info(
                "Added sunny_forecast_threshold_kwh: %.1f kWh",
                derived_threshold,
            )

        hass.config_entries.async_update_entry(
            entry,
            data=new_data,
            version=14,
        )

        _LOGGER.info("Migration to version 14 complete")

    return True
