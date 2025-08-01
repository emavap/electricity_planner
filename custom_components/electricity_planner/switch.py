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
    """Set up the switch platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    
    entities = [
        CarChargerControlSwitch(coordinator, entry),
        AutoChargingModeSwitch(coordinator, entry),
    ]
    
    async_add_entities(entities, False)


class ElectricityPlannerSwitchBase(CoordinatorEntity, SwitchEntity):
    """Base class for Electricity Planner switches."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self.entry = entry
        self._attr_has_entity_name = True
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "Electricity Planner",
            "manufacturer": "Custom",
            "model": "Electricity Planner",
            "sw_version": "1.0.0",
        }


class CarChargerControlSwitch(ElectricityPlannerSwitchBase):
    """Switch to control car charger based on recommendations."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry) -> None:
        """Initialize the car charger control switch."""
        super().__init__(coordinator, entry)
        self._attr_name = "Car Charger Control"
        self._attr_unique_id = f"{entry.entry_id}_car_charger_control"
        self._attr_icon = "mdi:car-electric"
        self._is_on = False

    @property
    def is_on(self) -> bool:
        """Return true if the switch is on."""
        return self._is_on

    @property
    def available(self) -> bool:
        """Return True if car charger entity is configured."""
        from .const import CONF_CAR_CHARGER_ENTITY
        return self.coordinator.config.get(CONF_CAR_CHARGER_ENTITY) is not None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on car charging."""
        success = await self.coordinator.set_car_charger_state(True)
        if success:
            self._is_on = True
            self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off car charging."""
        success = await self.coordinator.set_car_charger_state(False)
        if success:
            self._is_on = False
            self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        if not self.coordinator.data:
            return {}
        
        return {
            "car_charging_recommended": self.coordinator.data.get("car_charging_recommended"),
            "car_charging_reason": self.coordinator.data.get("car_charging_reason"),
        }


class AutoChargingModeSwitch(ElectricityPlannerSwitchBase):
    """Switch to enable/disable automatic charging mode."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry) -> None:
        """Initialize the auto charging mode switch."""
        super().__init__(coordinator, entry)
        self._attr_name = "Auto Charging Mode"
        self._attr_unique_id = f"{entry.entry_id}_auto_charging_mode"
        self._attr_icon = "mdi:auto-mode"
        self._is_on = True  # Default to enabled

    @property
    def is_on(self) -> bool:
        """Return true if auto mode is enabled."""
        return self._is_on

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable automatic charging mode."""
        self._is_on = True
        self.async_write_ha_state()
        _LOGGER.info("Auto charging mode enabled")

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable automatic charging mode."""
        self._is_on = False
        self.async_write_ha_state()
        _LOGGER.info("Auto charging mode disabled")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes."""
        return {
            "description": "Enable/disable automatic charging decisions",
            "mode": "automatic" if self._is_on else "manual",
        }