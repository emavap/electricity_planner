"""Number platform for Electricity Planner."""
from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from .const import (
    CONF_MAX_SOC_THRESHOLD,
    CONF_MAX_SOC_THRESHOLD_SUNNY,
    CONF_SOLAR_FORECAST_ENTITY_TOMORROW,
    CONF_SOLAR_FORECAST_START_HOUR,
    CONF_SOLAR_FORECAST_TODAY_ENTITY,
    CONF_SUNNY_FORECAST_THRESHOLD_KWH,
    DEFAULT_MAX_SOC,
    DEFAULT_MAX_SOC_SUNNY,
    DEFAULT_SOLAR_FORECAST_START_HOUR,
    DEFAULT_SUNNY_FORECAST_THRESHOLD_KWH,
    DOMAIN,
)
from .coordinator import ElectricityPlannerCoordinator

_LOGGER = logging.getLogger(__name__)
_NUMERIC_PREFIX_RE = re.compile(r"^\s*([-+]?\d+(?:[.,]\d+)?)")


def _parse_state_as_float(state_value: Any) -> float | None:
    """Parse Home Assistant state to float with tolerant formatting."""
    if state_value is None:
        return None
    raw_value = str(state_value).strip()
    try:
        return float(raw_value)
    except (TypeError, ValueError):
        match = _NUMERIC_PREFIX_RE.match(raw_value)
        if match:
            candidate = match.group(1).replace(",", ".")
            try:
                return float(candidate)
            except (TypeError, ValueError):
                return None
        return None


def _resolve_display_solar_forecast(coordinator: ElectricityPlannerCoordinator) -> float | None:
    """Best-effort forecast value for UI display when coordinator data is not populated yet."""
    if coordinator.data:
        from_data = coordinator.data.get("solar_forecast_production")
        if from_data is not None:
            return from_data

    now_local = dt_util.now()
    start_hour = int(
        coordinator.config.get(
            CONF_SOLAR_FORECAST_START_HOUR,
            DEFAULT_SOLAR_FORECAST_START_HOUR,
        )
    )

    primary_entity = (
        coordinator.config.get(CONF_SOLAR_FORECAST_ENTITY_TOMORROW)
        if now_local.hour >= start_hour
        else coordinator.config.get(CONF_SOLAR_FORECAST_TODAY_ENTITY)
    )

    if primary_entity:
        state = coordinator.hass.states.get(primary_entity)
        value = _parse_state_as_float(state.state if state else None)
        if value is not None:
            return value

    if now_local.hour < start_hour:
        cached_value = getattr(coordinator, "_cached_solar_forecast", None)
        if cached_value is not None:
            return float(cached_value)

    return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Electricity Planner number entities."""
    coordinator: ElectricityPlannerCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        MaxSocThresholdNumber(coordinator, entry),
        MaxSocThresholdSunnyNumber(coordinator, entry),
        SunnyForecastThresholdNumber(coordinator, entry),
    ]

    async_add_entities(entities)


class MaxSocThresholdNumber(CoordinatorEntity, NumberEntity):
    """Number entity to control maximum battery SOC threshold for grid charging.

    This threshold determines at what battery SOC level grid charging stops.
    For example, if set to 80%, the battery will only charge from grid until
    it reaches 80% SOC.
    """

    def __init__(
        self,
        coordinator: ElectricityPlannerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the max SOC threshold number."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_name = f"{entry.title} Battery Max SOC Threshold"
        self._attr_unique_id = f"{entry.entry_id}_max_soc_threshold"
        self.entity_id = f"number.{slugify(entry.title or 'electricity_planner')}_max_soc_threshold"
        self._attr_icon = "mdi:battery-charging-high"
        self._attr_native_unit_of_measurement = "%"
        self._attr_mode = NumberMode.SLIDER
        self._attr_native_min_value = 0
        self._attr_native_max_value = 100
        self._attr_native_step = 5
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Electricity Planner",
            "model": "Smart Charging Controller",
        }

    @property
    def native_value(self) -> float:
        """Return the current max SOC threshold value."""
        return self.coordinator.config.get(CONF_MAX_SOC_THRESHOLD, DEFAULT_MAX_SOC)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        current_value = self.native_value
        battery_analysis = self.coordinator.data.get("battery_analysis", {}) if self.coordinator.data else {}
        average_soc = battery_analysis.get("average_soc")

        attrs = {
            "description": (
                f"Battery will charge from grid until SOC reaches {current_value:.0f}%. "
                "Adjust this to control how full the battery should get when charging from grid."
            ),
        }

        if average_soc is not None:
            attrs["current_battery_soc"] = f"{average_soc:.1f}%"
            attrs["remaining_to_threshold"] = f"{max(0, current_value - average_soc):.1f}%"

        return attrs

    async def async_set_native_value(self, value: float) -> None:
        """Update the max SOC threshold value.

        This updates both the config entry options (persistent) and the
        coordinator config (immediate effect without reload).
        """
        new_value = int(value)
        _LOGGER.info("Updating battery max SOC threshold to %d%%", new_value)

        # Update config entry options for persistence
        new_options = dict(self._entry.options)
        new_options[CONF_MAX_SOC_THRESHOLD] = new_value

        self.hass.config_entries.async_update_entry(
            self._entry,
            options=new_options,
        )

        # Update coordinator config for immediate effect (no reload needed)
        self.coordinator.config[CONF_MAX_SOC_THRESHOLD] = new_value

        # Refresh decision engine settings to apply new threshold
        self.coordinator.decision_engine.refresh_settings(self.coordinator.config)

        # Trigger refresh to recalculate decisions with new threshold
        await self.coordinator.async_request_refresh()


class MaxSocThresholdSunnyNumber(CoordinatorEntity, NumberEntity):
    """Number entity to control the max SOC threshold for grid charging on high-solar days.

    When the solar forecast exceeds the configured sunny-day trigger threshold
    (kWh), this lower threshold is used instead of the standard max SOC
    threshold, preserving battery capacity for free solar energy.
    """

    def __init__(
        self,
        coordinator: ElectricityPlannerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sunny max SOC threshold number."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_name = f"{entry.title} Battery Max SOC Threshold (High Solar)"
        self._attr_unique_id = f"{entry.entry_id}_max_soc_threshold_sunny"
        self.entity_id = (
            f"number.{slugify(entry.title or 'electricity_planner')}_max_soc_threshold_sunny"
        )
        self._attr_icon = "mdi:weather-sunny"
        self._attr_native_unit_of_measurement = "%"
        self._attr_mode = NumberMode.SLIDER
        self._attr_native_min_value = 0
        self._attr_native_max_value = 100
        self._attr_native_step = 5
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Electricity Planner",
            "model": "Smart Charging Controller",
        }

    @property
    def native_value(self) -> float:
        """Return the current sunny max SOC threshold value."""
        return self.coordinator.config.get(CONF_MAX_SOC_THRESHOLD_SUNNY, DEFAULT_MAX_SOC_SUNNY)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        current_value = self.native_value
        normal_threshold = self.coordinator.config.get(CONF_MAX_SOC_THRESHOLD, DEFAULT_MAX_SOC)
        sunny_active = self.coordinator.data.get("sunny_day_active", False) if self.coordinator.data else False

        attrs = {
            "description": (
                f"When solar forecast is high, grid charging stops at {current_value:.0f}% "
                f"instead of {normal_threshold:.0f}%, leaving room for free solar."
            ),
            "sunny_day_active": sunny_active,
            "normal_threshold": f"{normal_threshold:.0f}%",
            "sunny_forecast_trigger_kwh": self.coordinator.config.get(
                CONF_SUNNY_FORECAST_THRESHOLD_KWH,
                DEFAULT_SUNNY_FORECAST_THRESHOLD_KWH,
            ),
        }

        solar_forecast = _resolve_display_solar_forecast(self.coordinator)
        if solar_forecast is not None:
            attrs["solar_forecast_kwh"] = f"{solar_forecast:.1f} kWh"

        return attrs

    async def async_set_native_value(self, value: float) -> None:
        """Update the sunny max SOC threshold value."""
        new_value = int(value)
        _LOGGER.info("Updating sunny max SOC threshold to %d%%", new_value)

        # Update config entry options for persistence
        new_options = dict(self._entry.options)
        new_options[CONF_MAX_SOC_THRESHOLD_SUNNY] = new_value

        self.hass.config_entries.async_update_entry(
            self._entry,
            options=new_options,
        )

        # Update coordinator config for immediate effect (no reload needed)
        self.coordinator.config[CONF_MAX_SOC_THRESHOLD_SUNNY] = new_value

        # Refresh decision engine settings to apply new threshold
        self.coordinator.decision_engine.refresh_settings(self.coordinator.config)

        # Trigger refresh to recalculate decisions with new threshold
        await self.coordinator.async_request_refresh()


class SunnyForecastThresholdNumber(CoordinatorEntity, NumberEntity):
    """Number entity for the kWh forecast trigger used to activate sunny mode."""

    def __init__(
        self,
        coordinator: ElectricityPlannerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the sunny forecast threshold number."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_name = f"{entry.title} Sunny Forecast Trigger"
        self._attr_unique_id = f"{entry.entry_id}_sunny_forecast_threshold_kwh"
        self.entity_id = (
            f"number.{slugify(entry.title or 'electricity_planner')}_sunny_forecast_threshold_kwh"
        )
        self._attr_icon = "mdi:weather-partly-cloudy"
        self._attr_native_unit_of_measurement = "kWh"
        self._attr_mode = NumberMode.SLIDER
        self._attr_native_min_value = 0
        self._attr_native_max_value = 100
        self._attr_native_step = 0.5
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Electricity Planner",
            "model": "Smart Charging Controller",
        }

    @property
    def native_value(self) -> float:
        """Return the current sunny forecast trigger threshold."""
        return self.coordinator.config.get(
            CONF_SUNNY_FORECAST_THRESHOLD_KWH,
            DEFAULT_SUNNY_FORECAST_THRESHOLD_KWH,
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        threshold = self.native_value
        solar_forecast = _resolve_display_solar_forecast(self.coordinator)
        attrs = {
            "description": (
                f"High-solar mode activates when forecast production is at least {threshold:.1f} kWh."
            ),
        }
        if solar_forecast is not None:
            attrs["solar_forecast_kwh"] = f"{solar_forecast:.1f} kWh"
            attrs["above_threshold"] = solar_forecast >= threshold
        return attrs

    async def async_set_native_value(self, value: float) -> None:
        """Update the sunny forecast trigger threshold in kWh."""
        new_value = max(0.0, float(value))
        _LOGGER.info("Updating sunny forecast trigger threshold to %.1f kWh", new_value)

        new_options = dict(self._entry.options)
        new_options[CONF_SUNNY_FORECAST_THRESHOLD_KWH] = new_value

        self.hass.config_entries.async_update_entry(
            self._entry,
            options=new_options,
        )

        self.coordinator.config[CONF_SUNNY_FORECAST_THRESHOLD_KWH] = new_value
        self.coordinator.decision_engine.refresh_settings(self.coordinator.config)
        await self.coordinator.async_request_refresh()
