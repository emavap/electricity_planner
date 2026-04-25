"""Number platform for Electricity Planner."""
from __future__ import annotations

import logging
import re
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from .const import (
    CONF_ARBITRAGE_MODE_DEADLINE_HOUR,
    CONF_ARBITRAGE_MODE_RESERVE_SOC,
    CONF_MAX_SOC_THRESHOLD,
    CONF_MAX_SOC_THRESHOLD_SUNNY,
    CONF_MAX_SOC_THRESHOLD_SOLAR,
    CONF_NEGATIVE_BUY_THRESHOLD,
    CONF_SOLAR_FORECAST_ENTITY_TOMORROW,
    CONF_SOLAR_FORECAST_START_HOUR,
    CONF_SOLAR_FORECAST_TODAY_ENTITY,
    CONF_SUNNY_FORECAST_THRESHOLD_KWH,
    DEFAULT_ARBITRAGE_MODE_DEADLINE_HOUR,
    DEFAULT_ARBITRAGE_MODE_RESERVE_SOC,
    DEFAULT_MAX_SOC,
    DEFAULT_MAX_SOC_SUNNY,
    DEFAULT_MAX_SOC_SOLAR,
    DEFAULT_NEGATIVE_BUY_THRESHOLD,
    DEFAULT_SOLAR_FORECAST_START_HOUR,
    DEFAULT_SUNNY_FORECAST_THRESHOLD_KWH,
    DOMAIN,
)
from .coordinator import ElectricityPlannerCoordinator

_LOGGER = logging.getLogger(__name__)
_NUMERIC_PREFIX_RE = re.compile(r"^\s*([-+]?\d+(?:[.,]\d+)?)")
_LIVE_NUMBER_OPTION_KEYS = (
    CONF_ARBITRAGE_MODE_DEADLINE_HOUR,
    CONF_ARBITRAGE_MODE_RESERVE_SOC,
    CONF_MAX_SOC_THRESHOLD,
    CONF_MAX_SOC_THRESHOLD_SUNNY,
    CONF_MAX_SOC_THRESHOLD_SOLAR,
    CONF_NEGATIVE_BUY_THRESHOLD,
    CONF_SUNNY_FORECAST_THRESHOLD_KWH,
)


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


def _build_updated_number_options(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: ElectricityPlannerCoordinator,
    option_key: str,
    option_value: Any,
) -> tuple[ConfigEntry, dict[str, Any]]:
    """Build an options payload without dropping sibling live number values."""
    latest_entry = hass.config_entries.async_get_entry(entry.entry_id) or entry

    merged_config: dict[str, Any] = dict(latest_entry.data)
    merged_config.update(latest_entry.options)
    for live_key in _LIVE_NUMBER_OPTION_KEYS:
        if live_key in coordinator.config:
            merged_config[live_key] = coordinator.config[live_key]
    merged_config[option_key] = option_value

    new_options = dict(latest_entry.options)
    for live_key in _LIVE_NUMBER_OPTION_KEYS:
        if live_key not in merged_config:
            new_options.pop(live_key, None)
            continue

        merged_value = merged_config[live_key]
        data_value = latest_entry.data.get(live_key)
        if (
            merged_value != data_value
            or live_key in latest_entry.options
            or live_key == option_key
        ):
            new_options[live_key] = merged_value
        else:
            new_options.pop(live_key, None)

    return latest_entry, new_options


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
        MaxSocThresholdSolarNumber(coordinator, entry),
        SunnyForecastThresholdNumber(coordinator, entry),
        ArbitrageModeReserveSocNumber(coordinator, entry),
        ArbitrageModeDeadlineHourNumber(coordinator, entry),
        NegativeBuyThresholdNumber(coordinator, entry),
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

        current_sunny_threshold = int(
            self.coordinator.config.get(
                CONF_MAX_SOC_THRESHOLD_SUNNY,
                DEFAULT_MAX_SOC_SUNNY,
            )
        )
        if new_value < current_sunny_threshold:
            raise HomeAssistantError(
                "Battery Max SOC Threshold must be greater than or equal to "
                f"High Solar Max SOC ({current_sunny_threshold}%)"
            )

        # Update config entry options for persistence
        self._entry, new_options = _build_updated_number_options(
            self.hass,
            self._entry,
            self.coordinator,
            CONF_MAX_SOC_THRESHOLD,
            new_value,
        )

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


class ArbitrageModeReserveSocNumber(CoordinatorEntity, NumberEntity):
    """Number entity controlling the reserve SOC for arbitrage mode."""

    def __init__(
        self,
        coordinator: ElectricityPlannerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the arbitrage reserve SOC number."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_name = f"{entry.title} Arbitrage Reserve SOC"
        # Preserve the legacy unique_id so historical statistics survive the
        # v22→v23 rename; the entity_id slug is updated in lockstep with
        # _async_migrate_entity_ids().
        self._attr_unique_id = f"{entry.entry_id}_battery_dump_target_soc"
        self.entity_id = (
            f"number.{slugify(entry.title or 'electricity_planner')}_arbitrage_mode_reserve_soc"
        )
        self._attr_icon = "mdi:battery-arrow-down-outline"
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
        """Return the current arbitrage reserve SOC."""
        return self.coordinator.config.get(
            CONF_ARBITRAGE_MODE_RESERVE_SOC,
            DEFAULT_ARBITRAGE_MODE_RESERVE_SOC,
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        attrs = {
            "description": (
                "When arbitrage mode is enabled, the planner exports the portion of "
                "battery energy above this reserve target while the current feed-in price "
                "is at or above the derived arbitrage threshold."
            ),
        }

        arbitrage_plan = self.coordinator.data.get("arbitrage_mode_plan", {}) if self.coordinator.data else {}
        average_soc = self.coordinator.data.get("battery_analysis", {}).get("average_soc") if self.coordinator.data else None

        if average_soc is not None:
            attrs["current_battery_soc"] = f"{average_soc:.1f}%"
            attrs["available_to_export_percent"] = f"{max(0, average_soc - self.native_value):.1f}%"

        if arbitrage_plan:
            attrs["arbitrage_mode_enabled"] = arbitrage_plan.get("enabled", False)
            attrs["arbitrage_price_threshold"] = arbitrage_plan.get("arbitrage_price_threshold")
            attrs["current_slot_price"] = arbitrage_plan.get("current_slot_price")
            attrs["selected_slots_count"] = arbitrage_plan.get("selected_slots_count", 0)
            attrs["slots_cover_full_arbitrage"] = arbitrage_plan.get("slots_cover_full_arbitrage", False)

        return attrs

    async def async_set_native_value(self, value: float) -> None:
        """Persist and apply the arbitrage reserve SOC immediately."""
        new_value = int(value)
        _LOGGER.info("Updating arbitrage reserve SOC to %d%%", new_value)

        self._entry, new_options = _build_updated_number_options(
            self.hass,
            self._entry,
            self.coordinator,
            CONF_ARBITRAGE_MODE_RESERVE_SOC,
            new_value,
        )

        self.hass.config_entries.async_update_entry(
            self._entry,
            options=new_options,
        )

        self.coordinator.config[CONF_ARBITRAGE_MODE_RESERVE_SOC] = new_value
        self.coordinator.decision_engine.refresh_settings(self.coordinator.config)
        await self.coordinator.async_request_refresh()


class ArbitrageModeDeadlineHourNumber(CoordinatorEntity, NumberEntity):
    """Number entity controlling the local deadline hour for arbitrage mode."""

    def __init__(
        self,
        coordinator: ElectricityPlannerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the arbitrage deadline hour number."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_name = f"{entry.title} Arbitrage Deadline Hour"
        self._attr_unique_id = f"{entry.entry_id}_arbitrage_mode_deadline_hour"
        self.entity_id = (
            f"number.{slugify(entry.title or 'electricity_planner')}_arbitrage_mode_deadline_hour"
        )
        self._attr_icon = "mdi:clock-end"
        self._attr_native_unit_of_measurement = "h"
        self._attr_mode = NumberMode.SLIDER
        self._attr_native_min_value = 0
        self._attr_native_max_value = 23
        self._attr_native_step = 1
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Electricity Planner",
            "model": "Smart Charging Controller",
        }

    @property
    def native_value(self) -> float:
        """Return the current arbitrage deadline hour."""
        return self.coordinator.config.get(
            CONF_ARBITRAGE_MODE_DEADLINE_HOUR,
            DEFAULT_ARBITRAGE_MODE_DEADLINE_HOUR,
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        return {
            "description": (
                "Local hour of the next arbitrage cutoff (shared by arbitrage selling "
                "and Negative Arbitrage Buy). Before it the planner targets today; "
                "after it, tomorrow."
            ),
        }

    async def async_set_native_value(self, value: float) -> None:
        """Persist and apply the arbitrage deadline hour immediately."""
        new_value = int(value)
        if new_value < 0 or new_value > 23:
            raise HomeAssistantError(
                f"Arbitrage Deadline Hour must be between 0 and 23 (got {new_value})"
            )
        _LOGGER.info("Updating arbitrage deadline hour to %d", new_value)

        self._entry, new_options = _build_updated_number_options(
            self.hass,
            self._entry,
            self.coordinator,
            CONF_ARBITRAGE_MODE_DEADLINE_HOUR,
            new_value,
        )

        self.hass.config_entries.async_update_entry(
            self._entry,
            options=new_options,
        )

        self.coordinator.config[CONF_ARBITRAGE_MODE_DEADLINE_HOUR] = new_value
        self.coordinator.decision_engine.refresh_settings(self.coordinator.config)
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

        current_normal_threshold = int(
            self.coordinator.config.get(
                CONF_MAX_SOC_THRESHOLD,
                DEFAULT_MAX_SOC,
            )
        )
        if new_value > current_normal_threshold:
            raise HomeAssistantError(
                "High Solar Max SOC must be less than or equal to "
                f"Battery Max SOC Threshold ({current_normal_threshold}%)"
            )

        # Update config entry options for persistence
        self._entry, new_options = _build_updated_number_options(
            self.hass,
            self._entry,
            self.coordinator,
            CONF_MAX_SOC_THRESHOLD_SUNNY,
            new_value,
        )

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


class MaxSocThresholdSolarNumber(CoordinatorEntity, NumberEntity):
    """Number entity controlling the battery SOC ceiling for solar absorption.

    This ceiling is independent of the grid-charging ``max_soc_threshold``:
    once batteries reach this SOC, further solar surplus is diverted to the
    EV or exported rather than continuing to fill the battery.
    """

    def __init__(
        self,
        coordinator: ElectricityPlannerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the solar max SOC threshold number."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_name = f"{entry.title} Battery Max SOC Threshold (Solar)"
        self._attr_unique_id = f"{entry.entry_id}_max_soc_threshold_solar"
        self.entity_id = (
            f"number.{slugify(entry.title or 'electricity_planner')}_max_soc_threshold_solar"
        )
        self._attr_icon = "mdi:solar-power-variant"
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
        """Return the current solar max SOC threshold value."""
        return self.coordinator.config.get(
            CONF_MAX_SOC_THRESHOLD_SOLAR, DEFAULT_MAX_SOC_SOLAR
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        current_value = self.native_value
        grid_threshold = self.coordinator.config.get(
            CONF_MAX_SOC_THRESHOLD, DEFAULT_MAX_SOC
        )
        battery_analysis = (
            self.coordinator.data.get("battery_analysis", {})
            if self.coordinator.data
            else {}
        )
        average_soc = battery_analysis.get("average_soc")

        attrs = {
            "description": (
                f"Solar is absorbed by the battery until SOC reaches {current_value:.0f}%. "
                "Above this, surplus is diverted to the EV or exported. "
                f"Grid charging target is {grid_threshold:.0f}%."
            ),
            "grid_threshold": f"{grid_threshold:.0f}%",
        }
        if average_soc is not None:
            attrs["current_battery_soc"] = f"{average_soc:.1f}%"
            attrs["solar_absorbing"] = average_soc < current_value
        return attrs

    async def async_set_native_value(self, value: float) -> None:
        """Update the solar max SOC threshold value."""
        new_value = int(value)
        _LOGGER.info("Updating solar max SOC threshold to %d%%", new_value)

        self._entry, new_options = _build_updated_number_options(
            self.hass,
            self._entry,
            self.coordinator,
            CONF_MAX_SOC_THRESHOLD_SOLAR,
            new_value,
        )

        self.hass.config_entries.async_update_entry(
            self._entry,
            options=new_options,
        )

        self.coordinator.config[CONF_MAX_SOC_THRESHOLD_SOLAR] = new_value
        self.coordinator.decision_engine.refresh_settings(self.coordinator.config)
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

        self._entry, new_options = _build_updated_number_options(
            self.hass,
            self._entry,
            self.coordinator,
            CONF_SUNNY_FORECAST_THRESHOLD_KWH,
            new_value,
        )

        self.hass.config_entries.async_update_entry(
            self._entry,
            options=new_options,
        )

        self.coordinator.config[CONF_SUNNY_FORECAST_THRESHOLD_KWH] = new_value
        self.coordinator.decision_engine.refresh_settings(self.coordinator.config)
        await self.coordinator.async_request_refresh()


class NegativeBuyThresholdNumber(CoordinatorEntity, NumberEntity):
    """Number entity controlling the Negative Arbitrage Buy price threshold.

    The planner treats any upcoming slot with net buy price ``<= threshold`` as
    paid-to-consume and forces grid charging during it. Default ``-0.05 €/kWh``
    targets only truly negative-priced slots.
    """

    def __init__(
        self,
        coordinator: ElectricityPlannerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the negative buy threshold number."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_name = f"{entry.title} Negative Arbitrage Buy Threshold"
        self._attr_unique_id = f"{entry.entry_id}_negative_buy_threshold"
        self.entity_id = (
            f"number.{slugify(entry.title or 'electricity_planner')}_negative_buy_threshold"
        )
        self._attr_icon = "mdi:cash-minus"
        self._attr_native_unit_of_measurement = "€/kWh"
        self._attr_mode = NumberMode.BOX
        self._attr_native_min_value = -1.0
        self._attr_native_max_value = 1.0
        self._attr_native_step = 0.01
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Electricity Planner",
            "model": "Smart Charging Controller",
        }

    @property
    def native_value(self) -> float:
        """Return the current Negative Arbitrage Buy price threshold."""
        return float(
            self.coordinator.config.get(
                CONF_NEGATIVE_BUY_THRESHOLD,
                DEFAULT_NEGATIVE_BUY_THRESHOLD,
            )
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        threshold = self.native_value
        buy_plan = (
            self.coordinator.data.get("negative_buy_plan", {})
            if self.coordinator.data
            else {}
        )
        attrs = {
            "description": (
                f"Negative Arbitrage Buy mode arms grid charging for any upcoming slot whose "
                f"net buy price is at or below {threshold:.3f} €/kWh, up to the shared "
                "arbitrage deadline."
            ),
        }
        if buy_plan:
            attrs["mode_enabled"] = buy_plan.get("enabled", False)
            attrs["currently_buying"] = buy_plan.get("active", False)
            attrs["selected_slots_count"] = buy_plan.get("selected_slots_count", 0)
            attrs["buy_price_threshold"] = buy_plan.get("buy_price_threshold")
            attrs["current_slot_price"] = buy_plan.get("current_slot_price")
            attrs["deadline"] = buy_plan.get("deadline")
        return attrs

    async def async_set_native_value(self, value: float) -> None:
        """Persist and apply the Negative Arbitrage Buy threshold immediately."""
        new_value = round(float(value), 3)
        _LOGGER.info(
            "Updating Negative Arbitrage Buy threshold to %.3f €/kWh", new_value
        )

        self._entry, new_options = _build_updated_number_options(
            self.hass,
            self._entry,
            self.coordinator,
            CONF_NEGATIVE_BUY_THRESHOLD,
            new_value,
        )

        self.hass.config_entries.async_update_entry(
            self._entry,
            options=new_options,
        )

        self.coordinator.config[CONF_NEGATIVE_BUY_THRESHOLD] = new_value
        self.coordinator.decision_engine.refresh_settings(self.coordinator.config)
        await self.coordinator.async_request_refresh()
