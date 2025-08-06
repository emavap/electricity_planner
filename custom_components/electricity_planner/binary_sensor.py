"""Binary sensor platform for Electricity Planner."""
from __future__ import annotations

from datetime import datetime

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
        DataAvailabilityBinarySensor(coordinator, entry),
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
        self._attr_name = "Battery: Charge from Grid"
        self._attr_unique_id = f"{entry.entry_id}_battery_grid_charging"
        self._attr_icon = "mdi:battery-charging"
        self._attr_device_class = BinarySensorDeviceClass.POWER

    @property
    def is_on(self) -> bool | None:
        """Return true if battery should be charged from grid."""
        if not self.coordinator.data:
            return False  # Safety: Never charge when no data available
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
        self._attr_name = "Car: Charge from Grid"
        self._attr_unique_id = f"{entry.entry_id}_car_grid_charging"
        self._attr_icon = "mdi:car-electric"
        self._attr_device_class = BinarySensorDeviceClass.POWER

    @property
    def is_on(self) -> bool | None:
        """Return true if car should be charged from grid."""
        if not self.coordinator.data:
            return False  # Safety: Never charge when no data available
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
        self._attr_name = "Price: Below Threshold"
        self._attr_unique_id = f"{entry.entry_id}_low_price"
        self._attr_icon = "mdi:currency-eur-off"
        self._attr_device_class = BinarySensorDeviceClass.POWER

    @property
    def is_on(self) -> bool | None:
        """Return true if electricity price is low."""
        if not self.coordinator.data or "price_analysis" not in self.coordinator.data:
            return False  # Safety: Assume price is not low when no data available
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
        self._attr_name = "Solar: Producing Power"
        self._attr_unique_id = f"{entry.entry_id}_solar_production"
        self._attr_icon = "mdi:solar-power"
        self._attr_device_class = BinarySensorDeviceClass.POWER

    @property
    def is_on(self) -> bool | None:
        """Return true if solar panels are producing power."""
        if not self.coordinator.data or "solar_analysis" not in self.coordinator.data:
            return False  # Safety: Assume no solar production when no data available
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


class DataAvailabilityBinarySensor(ElectricityPlannerBinarySensorBase):
    """Binary sensor for data availability status."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry) -> None:
        """Initialize the data availability binary sensor."""
        super().__init__(coordinator, entry)
        self._attr_name = "Data: Nord Pool Available"
        self._attr_unique_id = f"{entry.entry_id}_data_availability"
        self._attr_icon = "mdi:database-check"
        self._attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    @property
    def is_on(self) -> bool | None:
        """Return true if critical data is available."""
        if not self.coordinator.data:
            return False
        
        # Check if both price data and price analysis indicate data is available
        price_available = self.coordinator.data.get("current_price") is not None
        price_analysis_available = self.coordinator.data.get("price_analysis", {}).get("data_available", False)
        return price_available and price_analysis_available

    @property
    def extra_state_attributes(self) -> dict[str, any]:
        """Return the state attributes."""
        if not self.coordinator.data:
            return {
                "last_successful_update": None,
                "data_unavailable_since": None,
                "unavailable_duration_seconds": None,
            }
        
        attributes = {}
        
        # Add availability timestamps from coordinator
        if hasattr(self.coordinator, '_last_successful_update'):
            attributes["last_successful_update"] = self.coordinator._last_successful_update.isoformat()
        
        if hasattr(self.coordinator, '_data_unavailable_since') and self.coordinator._data_unavailable_since:
            attributes["data_unavailable_since"] = self.coordinator._data_unavailable_since.isoformat()
            unavailable_duration = (datetime.now() - self.coordinator._data_unavailable_since).total_seconds()
            attributes["unavailable_duration_seconds"] = int(unavailable_duration)
        
        # Add data source status
        price_analysis = self.coordinator.data.get("price_analysis", {})
        attributes.update({
            "current_price_available": self.coordinator.data.get("current_price") is not None,
            "highest_price_available": self.coordinator.data.get("highest_price") is not None,
            "lowest_price_available": self.coordinator.data.get("lowest_price") is not None,
            "next_price_available": self.coordinator.data.get("next_price") is not None,
            "price_analysis_available": price_analysis.get("data_available", False),
            "notification_sent": getattr(self.coordinator, '_notification_sent', False),
        })
        
        return attributes


