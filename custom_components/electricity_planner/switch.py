"""Switch platform for Electricity Planner."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ElectricityPlannerCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Electricity Planner switch entities."""
    coordinator: ElectricityPlannerCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        CarPermissiveModeSwitch(coordinator, entry),
    ]

    async_add_entities(entities)


class CarPermissiveModeSwitch(CoordinatorEntity, SwitchEntity):
    """Switch to enable permissive car charging mode (higher price threshold)."""

    def __init__(
        self,
        coordinator: ElectricityPlannerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the permissive mode switch."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_name = f"{entry.title} Car Permissive Mode"
        self._attr_unique_id = f"{entry.entry_id}_car_permissive_mode"
        self._attr_icon = "mdi:battery-alert"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Electricity Planner",
            "model": "Smart Charging Controller",
        }

    @property
    def is_on(self) -> bool:
        """Return true if permissive mode is enabled."""
        return self.coordinator.data.get("car_permissive_mode_active", False)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        multiplier = self.coordinator.config.get(
            "car_permissive_threshold_multiplier", 1.2
        )
        increase_pct = (multiplier - 1.0) * 100

        return {
            "threshold_multiplier": multiplier,
            "threshold_increase_percent": f"{increase_pct:.0f}%",
            "description": (
                f"When enabled, car charging threshold is increased by {increase_pct:.0f}% "
                "to allow charging at moderately higher prices"
            ),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on permissive mode.

        Args:
            **kwargs: Additional keyword arguments (unused)
        """
        _LOGGER.info("Enabling car permissive charging mode")
        # Safely update coordinator data with null check
        if self.coordinator.data is not None:
            self.coordinator.data["car_permissive_mode_active"] = True
        else:
            _LOGGER.warning("Cannot enable permissive mode - coordinator data not available")
            return
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off permissive mode.

        Args:
            **kwargs: Additional keyword arguments (unused)
        """
        _LOGGER.info("Disabling car permissive charging mode")
        # Safely update coordinator data with null check
        if self.coordinator.data is not None:
            self.coordinator.data["car_permissive_mode_active"] = False
        else:
            _LOGGER.warning("Cannot disable permissive mode - coordinator data not available")
            return
        await self.coordinator.async_request_refresh()
