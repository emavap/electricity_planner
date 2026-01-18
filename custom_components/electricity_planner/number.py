"""Number platform for Electricity Planner."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_MAX_SOC_THRESHOLD,
    DEFAULT_MAX_SOC,
    DOMAIN,
)
from .coordinator import ElectricityPlannerCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Electricity Planner number entities."""
    coordinator: ElectricityPlannerCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        MaxSocThresholdNumber(coordinator, entry),
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
        self._attr_icon = "mdi:battery-charging-high"
        self._attr_native_unit_of_measurement = "%"
        self._attr_mode = NumberMode.SLIDER
        self._attr_native_min_value = 50
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

