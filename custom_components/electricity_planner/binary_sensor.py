"""Binary sensor platform for Electricity Planner."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ElectricityPlannerCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the binary sensor platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    
    entities = [
        BatteryGridChargingBinarySensor(coordinator, entry),
        CarGridChargingBinarySensor(coordinator, entry),
        LowPriceBinarySensor(coordinator, entry),
        SolarProductionBinarySensor(coordinator, entry),
    ]
    
    async_add_entities(entities, False)


class ElectricityPlannerBinarySensorBase(CoordinatorEntity, BinarySensorEntity):
    """Base class for Electricity Planner binary sensors."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry) -> None:
        """Initialize the binary sensor."""
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


class BatteryGridChargingBinarySensor(ElectricityPlannerBinarySensorBase):
    """Binary sensor for battery grid charging recommendation."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry) -> None:
        """Initialize the battery grid charging binary sensor."""
        super().__init__(coordinator, entry)
        self._attr_name = "Battery Grid Charging"
        self._attr_unique_id = f"{entry.entry_id}_battery_grid_charging"
        self._attr_icon = "mdi:battery-charging"
        self._attr_device_class = BinarySensorDeviceClass.POWER

    @property
    def is_on(self) -> bool | None:
        """Return true if battery should be charged from grid."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("battery_grid_charging", False)

    @property
    def extra_state_attributes(self) -> dict[str, any]:
        """Return the state attributes."""
        if not self.coordinator.data:
            return {}
        return {
            "reason": self.coordinator.data.get("battery_grid_charging_reason"),
        }


class CarGridChargingBinarySensor(ElectricityPlannerBinarySensorBase):
    """Binary sensor for car grid charging recommendation."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry) -> None:
        """Initialize the car grid charging binary sensor."""
        super().__init__(coordinator, entry)
        self._attr_name = "Car Grid Charging"
        self._attr_unique_id = f"{entry.entry_id}_car_grid_charging"
        self._attr_icon = "mdi:car-electric"
        self._attr_device_class = BinarySensorDeviceClass.POWER

    @property
    def is_on(self) -> bool | None:
        """Return true if car should be charged from grid."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("car_grid_charging", False)

    @property
    def extra_state_attributes(self) -> dict[str, any]:
        """Return the state attributes."""
        if not self.coordinator.data:
            return {}
        return {
            "reason": self.coordinator.data.get("car_grid_charging_reason"),
        }


class LowPriceBinarySensor(ElectricityPlannerBinarySensorBase):
    """Binary sensor for low electricity price."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry) -> None:
        """Initialize the low price binary sensor."""
        super().__init__(coordinator, entry)
        self._attr_name = "Low Electricity Price"
        self._attr_unique_id = f"{entry.entry_id}_low_price"
        self._attr_icon = "mdi:currency-eur-off"
        self._attr_device_class = BinarySensorDeviceClass.POWER

    @property
    def is_on(self) -> bool | None:
        """Return true if electricity price is low."""
        if not self.coordinator.data or "price_analysis" not in self.coordinator.data:
            return None
        return self.coordinator.data["price_analysis"].get("is_low_price", False)

    @property
    def extra_state_attributes(self) -> dict[str, any]:
        """Return the state attributes."""
        if not self.coordinator.data or "price_analysis" not in self.coordinator.data:
            return {}
        
        price_analysis = self.coordinator.data["price_analysis"]
        return {
            "current_price": price_analysis.get("current_price"),
            "price_threshold": price_analysis.get("price_threshold"),
            "price_ratio": price_analysis.get("price_ratio"),
        }


class SolarProductionBinarySensor(ElectricityPlannerBinarySensorBase):
    """Binary sensor for solar production status."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry) -> None:
        """Initialize the solar production binary sensor."""
        super().__init__(coordinator, entry)
        self._attr_name = "Solar Production Active"
        self._attr_unique_id = f"{entry.entry_id}_solar_production"
        self._attr_icon = "mdi:solar-power"
        self._attr_device_class = BinarySensorDeviceClass.POWER

    @property
    def is_on(self) -> bool | None:
        """Return true if solar panels are producing power."""
        if not self.coordinator.data or "solar_analysis" not in self.coordinator.data:
            return None
        return self.coordinator.data["solar_analysis"].get("is_producing", False)

    @property
    def extra_state_attributes(self) -> dict[str, any]:
        """Return the state attributes."""
        if not self.coordinator.data or "solar_analysis" not in self.coordinator.data:
            return {}
        
        solar_analysis = self.coordinator.data["solar_analysis"]
        return {
            "current_production": solar_analysis.get("current_production"),
            "forecast": solar_analysis.get("forecast"),
            "has_good_forecast": solar_analysis.get("has_good_forecast"),
        }


