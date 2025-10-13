"""Binary sensor platform for Electricity Planner."""
from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, CONF_FEEDIN_PRICE_THRESHOLD, DEFAULT_FEEDIN_PRICE_THRESHOLD
from .coordinator import ElectricityPlannerCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the binary sensor platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    # AUTOMATION SENSORS: Essential binary sensors for automations (3/5 total automation sensors)
    automation_entities = [
        BatteryGridChargingBinarySensor(coordinator, entry, "_automation"),
        CarGridChargingBinarySensor(coordinator, entry, "_automation"),
        FeedinSolarBinarySensor(coordinator, entry, "_automation"),
    ]

    # DIAGNOSTIC SENSORS: For monitoring and troubleshooting only
    diagnostic_entities = [
        LowPriceBinarySensor(coordinator, entry, "_diagnostic"),
        SolarProductionBinarySensor(coordinator, entry, "_diagnostic"),
        DataAvailabilityBinarySensor(coordinator, entry, "_diagnostic"),
    ]

    entities = automation_entities + diagnostic_entities

    async_add_entities(entities, False)


class ElectricityPlannerBinarySensorBase(CoordinatorEntity, BinarySensorEntity):
    """Base class for Electricity Planner binary sensors."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry, device_suffix: str = "") -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        self.entry = entry
        self._attr_has_entity_name = True

        # Create device identifier with suffix for grouping
        device_id = f"{entry.entry_id}{device_suffix}"
        if device_suffix == "_automation":
            device_name = "Electricity Planner - Automation Controls"
        elif device_suffix == "_diagnostic":
            device_name = "Electricity Planner - Diagnostics & Monitoring"
        else:
            device_name = "Electricity Planner"

        self._attr_device_info = {
            "identifiers": {(DOMAIN, device_id)},
            "name": device_name,
            "manufacturer": "Custom",
            "model": "Electricity Planner",
            "sw_version": "1.0.0",
        }


class BatteryGridChargingBinarySensor(ElectricityPlannerBinarySensorBase):
    """AUTOMATION SENSOR (1/5): Battery grid charging decision."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry, device_suffix: str = "") -> None:
        """Initialize the battery grid charging binary sensor."""
        super().__init__(coordinator, entry, device_suffix)
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
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        if not self.coordinator.data:
            return {}
        return {
            "reason": self.coordinator.data.get("battery_grid_charging_reason"),
        }


class CarGridChargingBinarySensor(ElectricityPlannerBinarySensorBase):
    """AUTOMATION SENSOR (2/5): Car grid charging decision."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry, device_suffix: str = "") -> None:
        """Initialize the car grid charging binary sensor."""
        super().__init__(coordinator, entry, device_suffix)
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
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        if not self.coordinator.data:
            return {}
        return {
            "reason": self.coordinator.data.get("car_grid_charging_reason"),
        }


class LowPriceBinarySensor(ElectricityPlannerBinarySensorBase):
    """DIAGNOSTIC SENSOR: Low electricity price indicator."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry, device_suffix: str = "") -> None:
        """Initialize the low price binary sensor."""
        super().__init__(coordinator, entry, device_suffix)
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
    def extra_state_attributes(self) -> dict[str, Any]:
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
    """DIAGNOSTIC SENSOR: Solar production status indicator."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry, device_suffix: str = "") -> None:
        """Initialize the solar production binary sensor."""
        super().__init__(coordinator, entry, device_suffix)
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
    def extra_state_attributes(self) -> dict[str, Any]:
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
    """DIAGNOSTIC SENSOR: Nord Pool data availability status."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry, device_suffix: str = "") -> None:
        """Initialize the data availability binary sensor."""
        super().__init__(coordinator, entry, device_suffix)
        self._attr_name = "Data: Nord Pool Available"
        self._attr_unique_id = f"{entry.entry_id}_data_availability"
        self._attr_icon = "mdi:database-check"
        self._attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    @property
    def is_on(self) -> bool | None:
        """Return true if critical data is available."""
        if not self.coordinator.data:
            return False

        return self.coordinator.is_data_available()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        if not self.coordinator.data:
            return {
                "last_successful_update": None,
                "data_unavailable_since": None,
                "unavailable_duration_seconds": None,
            }

        attributes = {}

        # Add availability timestamps from coordinator
        if self.coordinator.last_successful_update:
            attributes["last_successful_update"] = self.coordinator.last_successful_update.isoformat()

        if self.coordinator.data_unavailable_since:
            attributes["data_unavailable_since"] = self.coordinator.data_unavailable_since.isoformat()
            unavailable_duration = (dt_util.utcnow() - self.coordinator.data_unavailable_since).total_seconds()
            attributes["unavailable_duration_seconds"] = int(unavailable_duration)

        # Add data source status
        price_analysis = self.coordinator.data.get("price_analysis", {})
        attributes.update({
            "current_price_available": self.coordinator.data.get("current_price") is not None,
            "highest_price_available": self.coordinator.data.get("highest_price") is not None,
            "lowest_price_available": self.coordinator.data.get("lowest_price") is not None,
            "next_price_available": self.coordinator.data.get("next_price") is not None,
            "price_analysis_available": price_analysis.get("data_available", False),
            "notification_sent": self.coordinator.notification_sent,
        })

        return attributes


class FeedinSolarBinarySensor(ElectricityPlannerBinarySensorBase):
    """AUTOMATION SENSOR (3/5): Solar feed-in decision."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry, device_suffix: str = "") -> None:
        """Initialize the feed-in solar binary sensor."""
        super().__init__(coordinator, entry, device_suffix)
        self._attr_name = "Solar: Feed-in Grid"
        self._attr_unique_id = f"{entry.entry_id}_feedin_solar"
        self._attr_icon = "mdi:solar-power-variant"
        self._attr_device_class = BinarySensorDeviceClass.POWER

    @property
    def is_on(self) -> bool | None:
        """Return true if solar feed-in should be enabled."""
        if not self.coordinator.data:
            return False
        return self.coordinator.data.get("feedin_solar", False)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        if not self.coordinator.data:
            return {}

        power_allocation = self.coordinator.data.get("power_allocation", {})
        return {
            "reason": self.coordinator.data.get("feedin_solar_reason", "No reason available"),
            "current_price": self.coordinator.data.get("current_price"),
            "feedin_threshold": self.entry.data.get(CONF_FEEDIN_PRICE_THRESHOLD, DEFAULT_FEEDIN_PRICE_THRESHOLD),
            "remaining_solar": power_allocation.get("remaining_solar", 0),
            "total_solar_allocated": power_allocation.get("total_allocated", 0),
        }
