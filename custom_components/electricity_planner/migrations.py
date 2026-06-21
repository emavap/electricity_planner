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
v14 → v15: Preserved legacy SOC defaults for sparse older entries after the
           default values changed for new installs.
v15 → v16: Replaced external ``transport_cost_entity`` with built-in cost
           components (P1 tariff entity, day/night network tariffs, taxes,
           GSC, WKK).  Removes the legacy ``transport_cost_entity`` key.
v16 → v17: Added ``max_inverter_power``, ``inverter_export_limit``, and
           ``inverter_derating_soc_bypass_threshold`` for inverter derating
           guidance.
v17 → v18: Added ``inverter_export_deadband`` so the derating hold window is
           configurable.
v18 → v19: Added ``inverter_derating_unused_release_minutes`` so the planner
           can release an unused cap after a configurable delay.
v19 → v20: Added ``battery_dump_deadline_hour`` so the arbitrage mode cutoff can
           be configured instead of always using noon.
v20 → v21: Added ``max_soc_threshold_solar`` so the solar-charging ceiling is
           independent from the grid-charging ``max_soc_threshold``.
v21 → v22: Added ``negative_buy_threshold`` for the Negative Arbitrage Buy mode
           (force grid-charge battery when net buy price drops below the
           threshold). The cutoff hour is shared with arbitrage selling via
           ``battery_dump_deadline_hour``; defensively removes the in-progress
           ``negative_buy_deadline_hour`` key from data and options if it was
           ever stored by a pre-release dev install.
v22 → v23: Renamed legacy ``battery_dump_*`` storage keys in ``entry.data`` and
           ``entry.options`` to their ``arbitrage_mode_*`` equivalents
           (``battery_dump_target_soc`` → ``arbitrage_mode_reserve_soc``,
           ``battery_dump_deadline_hour`` → ``arbitrage_mode_deadline_hour``,
           ``battery_dump_max_export_power`` → ``arbitrage_mode_max_export_power``).
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ARBITRAGE_MODE_DEADLINE_HOUR,
    CONF_ARBITRAGE_MODE_MAX_EXPORT_POWER,
    CONF_ARBITRAGE_MODE_RESERVE_SOC,
    CONF_BASE_GRID_SETPOINT,
    CONF_BATTERY_CAPACITIES,
    CONF_BATTERY_PHASE_ASSIGNMENTS,
    CONF_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
    CONF_DYNAMIC_THRESHOLD_CONFIDENCE,
    CONF_ENERGY_COST_GSC,
    CONF_ENERGY_COST_WKK,
    CONF_ENERGY_TAX_ACCIJNS,
    CONF_ENERGY_TAX_BIJDRAGE,
    CONF_FEEDIN_ADJUSTMENT_MULTIPLIER,
    CONF_FEEDIN_ADJUSTMENT_OFFSET,
    CONF_BUY_VAT_MULTIPLIER,
    CONF_INVERTER_DERATING_SOC_BYPASS_THRESHOLD,
    CONF_INVERTER_DERATING_UNUSED_RELEASE_MINUTES,
    CONF_INVERTER_EXPORT_DEADBAND,
    CONF_INVERTER_EXPORT_LIMIT,
    CONF_MAX_INVERTER_POWER,
    CONF_MAX_SOC_THRESHOLD,
    CONF_MAX_SOC_THRESHOLD_SOLAR,
    CONF_MAX_SOC_THRESHOLD_SUNNY,
    CONF_NEGATIVE_BUY_THRESHOLD,
    CONF_PHASE_MODE,
    CONF_PHASES,
    CONF_PRICE_ADJUSTMENT_MULTIPLIER,
    CONF_PRICE_ADJUSTMENT_OFFSET,
    CONF_SOC_BUFFER_TARGET,
    CONF_SOC_PRICE_MULTIPLIER_MAX,
    CONF_SOLAR_FORECAST_START_HOUR,
    CONF_SUNNY_FORECAST_THRESHOLD_KWH,
    CONF_TRANSPORT_COST_DAY,
    CONF_TRANSPORT_COST_ENTITY,
    CONF_TRANSPORT_COST_NIGHT,
    CONF_USE_DYNAMIC_THRESHOLD,
    DEFAULT_ARBITRAGE_MODE_DEADLINE_HOUR,
    DEFAULT_BASE_GRID_SETPOINT,
    DEFAULT_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
    DEFAULT_DYNAMIC_THRESHOLD_CONFIDENCE,
    DEFAULT_ENERGY_COST_GSC,
    DEFAULT_ENERGY_COST_WKK,
    DEFAULT_ENERGY_TAX_ACCIJNS,
    DEFAULT_ENERGY_TAX_BIJDRAGE,
    DEFAULT_FEEDIN_ADJUSTMENT_MULTIPLIER,
    DEFAULT_FEEDIN_ADJUSTMENT_OFFSET,
    DEFAULT_BUY_VAT_MULTIPLIER,
    DEFAULT_INVERTER_DERATING_SOC_BYPASS_THRESHOLD,
    DEFAULT_INVERTER_DERATING_UNUSED_RELEASE_MINUTES,
    DEFAULT_INVERTER_EXPORT_DEADBAND,
    DEFAULT_INVERTER_EXPORT_LIMIT,
    DEFAULT_MAX_INVERTER_POWER,
    DEFAULT_MAX_SOC_SOLAR,
    DEFAULT_NEGATIVE_BUY_THRESHOLD,
    DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER,
    DEFAULT_PRICE_ADJUSTMENT_OFFSET,
    DEFAULT_SOC_BUFFER_TARGET,
    DEFAULT_SOC_PRICE_MULTIPLIER_MAX,
    DEFAULT_SOLAR_FORECAST_START_HOUR,
    DEFAULT_SUNNY_FORECAST_THRESHOLD_KWH,
    DEFAULT_TRANSPORT_COST_DAY,
    DEFAULT_TRANSPORT_COST_NIGHT,
    DEFAULT_USE_DYNAMIC_THRESHOLD,
    PHASE_MODE_SINGLE,
)
from .helpers import coerce_integral_range

_LOGGER = logging.getLogger(__name__)

_LEGACY_DEFAULT_MAX_SOC = 90
_LEGACY_DEFAULT_MAX_SOC_SUNNY = 50

# Current config version
CURRENT_VERSION = 24


def _validate_numeric_config(
    config: dict[str, Any],
    key: str,
    min_val: float,
    max_val: float,
    default: float,
    name: str,
) -> None:
    """Validate and clamp numeric configuration values."""
    if key in config:
        try:
            value = float(config[key])
            if not min_val <= value <= max_val:
                _LOGGER.warning(
                    "Migration: %s value %.2f out of range [%.2f, %.2f], resetting to default %.2f",
                    name,
                    value,
                    min_val,
                    max_val,
                    default,
                )
                config[key] = default
        except (TypeError, ValueError):
            _LOGGER.warning(
                "Migration: %s value %s invalid, resetting to default %.2f",
                name,
                config[key],
                default,
            )
            config[key] = default


def _normalize_integral_config(
    config: dict[str, Any],
    key: str,
    min_val: int,
    max_val: int,
    default: int,
    name: str,
) -> None:
    """Validate an integral config value and reset invalid values to the default."""
    if key not in config:
        return

    normalized = coerce_integral_range(
        config[key],
        min_value=min_val,
        max_value=max_val,
    )
    if normalized is None:
        _LOGGER.warning(
            "Migration: %s value %s invalid, resetting to default %d",
            name,
            config[key],
            default,
        )
        config[key] = default
        return

    config[key] = normalized


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
            new_data,
            CONF_BASE_GRID_SETPOINT,
            1000,
            15000,
            DEFAULT_BASE_GRID_SETPOINT,
            "base_grid_setpoint",
        )

        # Update entry with new data
        hass.config_entries.async_update_entry(entry, data=new_data, version=2)

        _LOGGER.info("Migration to version 2 complete")

    if entry.version == 2:
        # Migrate from version 2 to version 3
        new_data = {**entry.data}

        # Add dynamic threshold configuration options
        if CONF_USE_DYNAMIC_THRESHOLD not in new_data:
            new_data[CONF_USE_DYNAMIC_THRESHOLD] = DEFAULT_USE_DYNAMIC_THRESHOLD
            _LOGGER.info(
                "Added use_dynamic_threshold: %s", DEFAULT_USE_DYNAMIC_THRESHOLD
            )

        if CONF_DYNAMIC_THRESHOLD_CONFIDENCE not in new_data:
            new_data[CONF_DYNAMIC_THRESHOLD_CONFIDENCE] = (
                DEFAULT_DYNAMIC_THRESHOLD_CONFIDENCE
            )
            _LOGGER.info(
                "Added dynamic_threshold_confidence: %s%%",
                DEFAULT_DYNAMIC_THRESHOLD_CONFIDENCE,
            )

        # Update entry with new data
        hass.config_entries.async_update_entry(entry, data=new_data, version=3)

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
            _LOGGER.info(
                "Removed %d deprecated time-based config options", removed_count
            )

        # Update entry with cleaned data
        hass.config_entries.async_update_entry(entry, data=new_data, version=4)

        _LOGGER.info("Migration to version 4 complete")

    if entry.version == 4:
        # Migrate from version 4 to version 5
        new_data = {**entry.data}

        # Remove unused config option that was never used in decision logic
        if "grid_battery_charging_limit_soc" in new_data:
            del new_data["grid_battery_charging_limit_soc"]
            _LOGGER.info("Removed unused config: grid_battery_charging_limit_soc")

        # Update entry with cleaned data
        hass.config_entries.async_update_entry(entry, data=new_data, version=5)

        _LOGGER.info("Migration to version 5 complete")

    if entry.version == 5:
        # Migrate from version 5 to version 6
        new_data = {**entry.data}

        if CONF_PRICE_ADJUSTMENT_MULTIPLIER not in new_data:
            new_data[CONF_PRICE_ADJUSTMENT_MULTIPLIER] = (
                DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER
            )

        if CONF_PRICE_ADJUSTMENT_OFFSET not in new_data:
            new_data[CONF_PRICE_ADJUSTMENT_OFFSET] = DEFAULT_PRICE_ADJUSTMENT_OFFSET

        if CONF_FEEDIN_ADJUSTMENT_MULTIPLIER not in new_data:
            new_data[CONF_FEEDIN_ADJUSTMENT_MULTIPLIER] = (
                DEFAULT_FEEDIN_ADJUSTMENT_MULTIPLIER
            )

        if CONF_FEEDIN_ADJUSTMENT_OFFSET not in new_data:
            new_data[CONF_FEEDIN_ADJUSTMENT_OFFSET] = DEFAULT_FEEDIN_ADJUSTMENT_OFFSET

        hass.config_entries.async_update_entry(entry, data=new_data, version=6)

        _LOGGER.info("Migration to version 6 complete")

    if entry.version == 6:
        # Migrate from version 6 to version 7
        # No data changes needed - only adds optional nordpool_config_entry field
        # which will be added through UI if user chooses to configure it
        hass.config_entries.async_update_entry(entry, data={**entry.data}, version=7)
        _LOGGER.info("Migration to version 7 complete")

    if entry.version == 7:
        # Migrate from version 7 to version 8
        new_data = {**entry.data}

        # Add car permissive mode multiplier configuration
        if CONF_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER not in new_data:
            new_data[CONF_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER] = (
                DEFAULT_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER
            )
            _LOGGER.info(
                "Added car_permissive_threshold_multiplier: %.1f",
                DEFAULT_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
            )

        hass.config_entries.async_update_entry(entry, data=new_data, version=8)

        _LOGGER.info("Migration to version 8 complete")

    if entry.version == 8:
        # Migrate from version 8 to version 9
        new_data = {**entry.data}

        if CONF_PHASE_MODE not in new_data:
            new_data[CONF_PHASE_MODE] = PHASE_MODE_SINGLE
            _LOGGER.info("Set default phase_mode to single-phase")

        new_data.setdefault(CONF_PHASES, {})
        new_data.setdefault(CONF_BATTERY_PHASE_ASSIGNMENTS, {})

        hass.config_entries.async_update_entry(entry, data=new_data, version=9)

        _LOGGER.info("Migration to version 9 complete")

    if entry.version == 9:
        # Migrate from version 9 to version 10
        # No data changes needed - version 10 adds optional battery_power_entity per phase
        # which will be added through UI if user chooses to configure it
        hass.config_entries.async_update_entry(entry, data={**entry.data}, version=10)
        _LOGGER.info("Migration to version 10 complete")

    if entry.version == 10:
        # Migrate from version 10 to version 11
        new_data = {**entry.data}

        # Add SOC-based price multiplier settings
        if CONF_SOC_PRICE_MULTIPLIER_MAX not in new_data:
            new_data[CONF_SOC_PRICE_MULTIPLIER_MAX] = DEFAULT_SOC_PRICE_MULTIPLIER_MAX
            _LOGGER.info(
                "Added soc_price_multiplier_max: %.2f", DEFAULT_SOC_PRICE_MULTIPLIER_MAX
            )

        if CONF_SOC_BUFFER_TARGET not in new_data:
            new_data[CONF_SOC_BUFFER_TARGET] = DEFAULT_SOC_BUFFER_TARGET
            _LOGGER.info("Added soc_buffer_target: %d%%", DEFAULT_SOC_BUFFER_TARGET)

        hass.config_entries.async_update_entry(entry, data=new_data, version=11)

        _LOGGER.info("Migration to version 11 complete")

    if entry.version == 11:
        # Migrate from version 11 to version 12
        new_data = {**entry.data}

        # Add sunny-day grid charging limit
        if CONF_MAX_SOC_THRESHOLD_SUNNY not in new_data:
            new_data[CONF_MAX_SOC_THRESHOLD_SUNNY] = _LEGACY_DEFAULT_MAX_SOC_SUNNY
            _LOGGER.info(
                "Added max_soc_threshold_sunny: %d%%",
                _LEGACY_DEFAULT_MAX_SOC_SUNNY,
            )

        # solar_forecast_entity is optional, no default needed

        hass.config_entries.async_update_entry(entry, data=new_data, version=12)

        _LOGGER.info("Migration to version 12 complete")

    if entry.version == 12:
        # Migrate from version 12 to version 13
        new_data = {**entry.data}

        # Add configurable solar forecast start hour
        if CONF_SOLAR_FORECAST_START_HOUR not in new_data:
            new_data[CONF_SOLAR_FORECAST_START_HOUR] = DEFAULT_SOLAR_FORECAST_START_HOUR
            _LOGGER.info(
                "Added solar_forecast_start_hour: %d", DEFAULT_SOLAR_FORECAST_START_HOUR
            )

        hass.config_entries.async_update_entry(entry, data=new_data, version=13)

        _LOGGER.info("Migration to version 13 complete")

    if entry.version == 13:
        # Migrate from version 13 to version 14
        new_data = {**entry.data}
        merged_config = {**entry.data, **entry.options}

        if CONF_SUNNY_FORECAST_THRESHOLD_KWH not in new_data:
            derived_threshold = _derive_sunny_forecast_threshold_kwh(merged_config)
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

    if entry.version == 14:
        # Migrate from version 14 to version 15
        # Preserve the historical 90/50 SOC defaults for legacy sparse entries
        # that never explicitly stored these values.
        new_data = {**entry.data}
        merged_config = {**entry.data, **entry.options}

        if CONF_MAX_SOC_THRESHOLD not in merged_config:
            new_data[CONF_MAX_SOC_THRESHOLD] = _LEGACY_DEFAULT_MAX_SOC
            _LOGGER.info(
                "Backfilled legacy max_soc_threshold: %d%%",
                _LEGACY_DEFAULT_MAX_SOC,
            )

        if CONF_MAX_SOC_THRESHOLD_SUNNY not in merged_config:
            new_data[CONF_MAX_SOC_THRESHOLD_SUNNY] = _LEGACY_DEFAULT_MAX_SOC_SUNNY
            _LOGGER.info(
                "Backfilled legacy max_soc_threshold_sunny: %d%%",
                _LEGACY_DEFAULT_MAX_SOC_SUNNY,
            )

        hass.config_entries.async_update_entry(
            entry,
            data=new_data,
            version=15,
        )

        _LOGGER.info("Migration to version 15 complete")

    if entry.version == 15:
        # Migrate from version 15 to version 16
        # Replace external transport_cost_entity with built-in cost components
        new_data = {**entry.data}

        # Add built-in transport cost defaults
        if CONF_TRANSPORT_COST_DAY not in new_data:
            new_data[CONF_TRANSPORT_COST_DAY] = DEFAULT_TRANSPORT_COST_DAY
        if CONF_TRANSPORT_COST_NIGHT not in new_data:
            new_data[CONF_TRANSPORT_COST_NIGHT] = DEFAULT_TRANSPORT_COST_NIGHT
        if CONF_ENERGY_TAX_ACCIJNS not in new_data:
            new_data[CONF_ENERGY_TAX_ACCIJNS] = DEFAULT_ENERGY_TAX_ACCIJNS
        if CONF_ENERGY_TAX_BIJDRAGE not in new_data:
            new_data[CONF_ENERGY_TAX_BIJDRAGE] = DEFAULT_ENERGY_TAX_BIJDRAGE
        if CONF_ENERGY_COST_GSC not in new_data:
            new_data[CONF_ENERGY_COST_GSC] = DEFAULT_ENERGY_COST_GSC
        if CONF_ENERGY_COST_WKK not in new_data:
            new_data[CONF_ENERGY_COST_WKK] = DEFAULT_ENERGY_COST_WKK

        # Remove legacy transport_cost_entity
        legacy_entity = new_data.pop(CONF_TRANSPORT_COST_ENTITY, None)
        if legacy_entity:
            _LOGGER.info(
                "Removed legacy transport_cost_entity (%s) — replaced by built-in cost components",
                legacy_entity,
            )

        _LOGGER.info(
            "Added built-in transport cost: day=%.4f, night=%.4f, accijns=%.6f, bijdrage=%.6f, gsc=%.4f, wkk=%.4f",
            new_data[CONF_TRANSPORT_COST_DAY],
            new_data[CONF_TRANSPORT_COST_NIGHT],
            new_data[CONF_ENERGY_TAX_ACCIJNS],
            new_data[CONF_ENERGY_TAX_BIJDRAGE],
            new_data[CONF_ENERGY_COST_GSC],
            new_data[CONF_ENERGY_COST_WKK],
        )

        hass.config_entries.async_update_entry(
            entry,
            data=new_data,
            version=16,
        )

        _LOGGER.info("Migration to version 16 complete")

    if entry.version == 16:
        # Migrate from version 16 to version 17
        # Add inverter derating configuration options
        new_data = {**entry.data}

        if CONF_MAX_INVERTER_POWER not in new_data:
            new_data[CONF_MAX_INVERTER_POWER] = DEFAULT_MAX_INVERTER_POWER
            _LOGGER.info("Added max_inverter_power: %dW", DEFAULT_MAX_INVERTER_POWER)

        if CONF_INVERTER_EXPORT_LIMIT not in new_data:
            new_data[CONF_INVERTER_EXPORT_LIMIT] = DEFAULT_INVERTER_EXPORT_LIMIT
            _LOGGER.info(
                "Added inverter_export_limit: %dW", DEFAULT_INVERTER_EXPORT_LIMIT
            )

        if CONF_INVERTER_DERATING_SOC_BYPASS_THRESHOLD not in new_data:
            new_data[CONF_INVERTER_DERATING_SOC_BYPASS_THRESHOLD] = (
                DEFAULT_INVERTER_DERATING_SOC_BYPASS_THRESHOLD
            )
            _LOGGER.info(
                "Added inverter_derating_soc_bypass_threshold: %d%%",
                DEFAULT_INVERTER_DERATING_SOC_BYPASS_THRESHOLD,
            )

        hass.config_entries.async_update_entry(
            entry,
            data=new_data,
            version=17,
        )

        _LOGGER.info("Migration to version 17 complete")

    if entry.version == 17:
        new_data = {**entry.data}

        if CONF_INVERTER_EXPORT_DEADBAND not in new_data:
            new_data[CONF_INVERTER_EXPORT_DEADBAND] = DEFAULT_INVERTER_EXPORT_DEADBAND
            _LOGGER.info(
                "Added inverter_export_deadband: %dW",
                DEFAULT_INVERTER_EXPORT_DEADBAND,
            )

        hass.config_entries.async_update_entry(
            entry,
            data=new_data,
            version=18,
        )

        _LOGGER.info("Migration to version 18 complete")

    if entry.version == 18:
        new_data = {**entry.data}

        if CONF_INVERTER_DERATING_UNUSED_RELEASE_MINUTES not in new_data:
            new_data[CONF_INVERTER_DERATING_UNUSED_RELEASE_MINUTES] = (
                DEFAULT_INVERTER_DERATING_UNUSED_RELEASE_MINUTES
            )
            _LOGGER.info(
                "Added inverter_derating_unused_release_minutes: %d",
                DEFAULT_INVERTER_DERATING_UNUSED_RELEASE_MINUTES,
            )

        hass.config_entries.async_update_entry(
            entry,
            data=new_data,
            version=19,
        )

        _LOGGER.info("Migration to version 19 complete")

    if entry.version == 19:
        # Historical migration: stored under the legacy key
        # ``battery_dump_deadline_hour``; the v22 → v23 migration below renames
        # it to ``arbitrage_mode_deadline_hour``.
        new_data = {**entry.data}
        legacy_key = "battery_dump_deadline_hour"

        if legacy_key not in new_data:
            new_data[legacy_key] = DEFAULT_ARBITRAGE_MODE_DEADLINE_HOUR
            _LOGGER.info(
                "Added battery_dump_deadline_hour: %d",
                DEFAULT_ARBITRAGE_MODE_DEADLINE_HOUR,
            )

        _normalize_integral_config(
            new_data,
            legacy_key,
            0,
            23,
            DEFAULT_ARBITRAGE_MODE_DEADLINE_HOUR,
            "battery_dump_deadline_hour",
        )

        hass.config_entries.async_update_entry(
            entry,
            data=new_data,
            version=20,
        )

        _LOGGER.info("Migration to version 20 complete")

    if entry.version == 20:
        new_data = {**entry.data}

        if CONF_MAX_SOC_THRESHOLD_SOLAR not in new_data:
            new_data[CONF_MAX_SOC_THRESHOLD_SOLAR] = DEFAULT_MAX_SOC_SOLAR
            _LOGGER.info(
                "Added max_soc_threshold_solar: %d%%",
                DEFAULT_MAX_SOC_SOLAR,
            )

        _validate_numeric_config(
            new_data,
            CONF_MAX_SOC_THRESHOLD_SOLAR,
            0,
            100,
            DEFAULT_MAX_SOC_SOLAR,
            "max_soc_threshold_solar",
        )

        hass.config_entries.async_update_entry(
            entry,
            data=new_data,
            version=21,
        )

        _LOGGER.info("Migration to version 21 complete")

    if entry.version == 21:
        new_data = {**entry.data}
        new_options = {**entry.options}

        if CONF_NEGATIVE_BUY_THRESHOLD not in new_data:
            new_data[CONF_NEGATIVE_BUY_THRESHOLD] = DEFAULT_NEGATIVE_BUY_THRESHOLD
            _LOGGER.info(
                "Added negative_buy_threshold: %.3f€/kWh",
                DEFAULT_NEGATIVE_BUY_THRESHOLD,
            )

        # Defensive cleanup: pre-release dev installs may have stored an
        # independent ``negative_buy_deadline_hour`` before it was unified
        # with the arbitrage cutoff (``battery_dump_deadline_hour``).
        if new_data.pop("negative_buy_deadline_hour", None) is not None:
            _LOGGER.info(
                "Removed obsolete negative_buy_deadline_hour from data — unified"
                " with battery_dump_deadline_hour"
            )
        if new_options.pop("negative_buy_deadline_hour", None) is not None:
            _LOGGER.info(
                "Removed obsolete negative_buy_deadline_hour from options —"
                " unified with battery_dump_deadline_hour"
            )

        hass.config_entries.async_update_entry(
            entry,
            data=new_data,
            options=new_options,
            version=22,
        )

        _LOGGER.info("Migration to version 22 complete")

    if entry.version == 22:
        # Rename legacy ``battery_dump_*`` keys to ``arbitrage_mode_*`` in both
        # ``entry.data`` and ``entry.options``. Internal-only keys; users never
        # type these.
        new_data = {**entry.data}
        new_options = {**entry.options}

        rename_map = {
            "battery_dump_target_soc": CONF_ARBITRAGE_MODE_RESERVE_SOC,
            "battery_dump_deadline_hour": CONF_ARBITRAGE_MODE_DEADLINE_HOUR,
            "battery_dump_max_export_power": CONF_ARBITRAGE_MODE_MAX_EXPORT_POWER,
        }

        for source_dict in (new_data, new_options):
            for legacy_key, new_key in rename_map.items():
                if legacy_key in source_dict and new_key not in source_dict:
                    source_dict[new_key] = source_dict.pop(legacy_key)
                    _LOGGER.info(
                        "Renamed %s → %s",
                        legacy_key,
                        new_key,
                    )
                elif legacy_key in source_dict:
                    # New key already present; drop the legacy duplicate.
                    source_dict.pop(legacy_key)

        hass.config_entries.async_update_entry(
            entry,
            data=new_data,
            options=new_options,
            version=23,
        )

        _LOGGER.info("Migration to version 23 complete")

    if entry.version == 23:
        # v23 → v24: add buy_vat_multiplier to options and data with default 1.06
        new_data = {**entry.data}
        new_options = {**entry.options}

        if CONF_BUY_VAT_MULTIPLIER not in new_data:
            new_data[CONF_BUY_VAT_MULTIPLIER] = DEFAULT_BUY_VAT_MULTIPLIER
        if CONF_BUY_VAT_MULTIPLIER not in new_options:
            new_options[CONF_BUY_VAT_MULTIPLIER] = DEFAULT_BUY_VAT_MULTIPLIER

        hass.config_entries.async_update_entry(
            entry,
            data=new_data,
            options=new_options,
            version=24,
        )

        _LOGGER.info("Migration to version 24 complete")

    return True
