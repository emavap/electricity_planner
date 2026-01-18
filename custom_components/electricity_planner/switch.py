"""Switch platform for Electricity Planner."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
    DEFAULT_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
    DOMAIN,
)
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
        BatteryChargingDisableSwitch(coordinator, entry),
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
        self._attr_name = f"{entry.title} Car Permissive Mode"
        self._attr_unique_id = f"{entry.entry_id}_car_permissive_mode"
        self._attr_icon = "mdi:car-electric-outline"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Electricity Planner",
            "model": "Smart Charging Controller",
        }

    @property
    def is_on(self) -> bool:
        """Return true if permissive mode is enabled."""
        if self.coordinator.data is None:
            return False
        return self.coordinator.data.get("car_permissive_mode_active", False)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        multiplier = self.coordinator.config.get(
            CONF_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
            DEFAULT_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
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


class BatteryChargingDisableSwitch(CoordinatorEntity, SwitchEntity):
    """Switch to manually disable battery grid charging.

    When ON, battery charging from grid is forcibly disabled regardless of
    price conditions. Works for both single-phase and three-phase configurations.
    """

    def __init__(
        self,
        coordinator: ElectricityPlannerCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the battery charging disable switch."""
        super().__init__(coordinator)
        self._attr_name = f"{entry.title} Disable Battery Charging"
        self._attr_unique_id = f"{entry.entry_id}_disable_battery_charging"
        self._attr_icon = "mdi:battery-off"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": entry.title,
            "manufacturer": "Electricity Planner",
            "model": "Smart Charging Controller",
        }

    @property
    def is_on(self) -> bool:
        """Return true if battery charging is disabled (override active)."""
        override = self.coordinator.get_manual_override("battery_grid_charging")
        if override is None:
            return False
        # Check if the override is forcing charging OFF (disabled)
        return override.get("value") is False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        override = self.coordinator.get_manual_override("battery_grid_charging")
        if override and override.get("value") is False:
            set_at = override.get("set_at")
            expires_at = override.get("expires_at")
            return {
                "override_active": True,
                "reason": override.get("reason", "Manual disable"),
                "set_at": set_at.isoformat() if set_at else None,
                "expires_at": expires_at.isoformat() if expires_at else "Never",
                "description": (
                    "Battery charging from grid is manually disabled. "
                    "Turn off this switch to resume automatic charging decisions."
                ),
            }
        return {
            "override_active": False,
            "description": (
                "Turn on to manually prevent battery from charging from the grid. "
                "This overrides all automatic charging decisions."
            ),
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on to disable battery charging.

        Args:
            **kwargs: Additional keyword arguments (unused)
        """
        _LOGGER.info("Manually disabling battery grid charging")
        await self.coordinator.async_set_manual_override(
            target="battery",
            value=False,  # Force charging OFF
            duration=None,  # No expiration
            reason="Manual disable via dashboard switch",
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off to resume automatic charging decisions.

        Args:
            **kwargs: Additional keyword arguments (unused)
        """
        _LOGGER.info("Clearing manual battery charging disable - resuming automatic mode")
        await self.coordinator.async_clear_manual_override(target="battery")
        await self.coordinator.async_request_refresh()
