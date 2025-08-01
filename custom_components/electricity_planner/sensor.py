"""Sensor platform for Electricity Planner."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
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
    """Set up the sensor platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    
    entities = [
        ChargingDecisionSensor(coordinator, entry),
        BatteryAnalysisSensor(coordinator, entry),
        PriceAnalysisSensor(coordinator, entry),
        PowerAnalysisSensor(coordinator, entry),
    ]
    
    async_add_entities(entities, False)


class ElectricityPlannerSensorBase(CoordinatorEntity, SensorEntity):
    """Base class for Electricity Planner sensors."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
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


class ChargingDecisionSensor(ElectricityPlannerSensorBase):
    """Sensor for grid charging decision status."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry) -> None:
        """Initialize the charging decision sensor."""
        super().__init__(coordinator, entry)
        self._attr_name = "Grid Charging Decision"
        self._attr_unique_id = f"{entry.entry_id}_grid_charging_decision"
        self._attr_icon = "mdi:transmission-tower"

    @property
    def native_value(self) -> str:
        """Return the state of the sensor."""
        if not self.coordinator.data:
            return "unknown"
        
        battery_grid = self.coordinator.data.get("battery_grid_charging", False)
        car_grid = self.coordinator.data.get("car_grid_charging", False)
        
        if battery_grid and car_grid:
            return "charge_both_from_grid"
        elif battery_grid:
            return "charge_battery_from_grid"
        elif car_grid:
            return "charge_car_from_grid"
        else:
            return "no_grid_charging"

    @property
    def extra_state_attributes(self) -> dict[str, any]:
        """Return the state attributes."""
        if not self.coordinator.data:
            return {}
        
        return {
            "battery_grid_charging": self.coordinator.data.get("battery_grid_charging"),
            "car_grid_charging": self.coordinator.data.get("car_grid_charging"),
            "battery_grid_charging_reason": self.coordinator.data.get("battery_grid_charging_reason"),
            "car_grid_charging_reason": self.coordinator.data.get("car_grid_charging_reason"),
            "next_evaluation": self.coordinator.data.get("next_evaluation"),
        }


class BatteryAnalysisSensor(ElectricityPlannerSensorBase):
    """Sensor for battery analysis."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry) -> None:
        """Initialize the battery analysis sensor."""
        super().__init__(coordinator, entry)
        self._attr_name = "Battery Analysis"
        self._attr_unique_id = f"{entry.entry_id}_battery_analysis"
        self._attr_icon = "mdi:battery"
        self._attr_device_class = SensorDeviceClass.BATTERY
        self._attr_native_unit_of_measurement = "%"

    @property
    def native_value(self) -> float | None:
        """Return the average battery SOC."""
        if not self.coordinator.data or "battery_analysis" not in self.coordinator.data:
            return None
        
        return self.coordinator.data["battery_analysis"].get("average_soc")

    @property
    def extra_state_attributes(self) -> dict[str, any]:
        """Return the state attributes."""
        if not self.coordinator.data or "battery_analysis" not in self.coordinator.data:
            return {}
        
        battery_analysis = self.coordinator.data["battery_analysis"]
        return {
            "min_soc": battery_analysis.get("min_soc"),
            "max_soc": battery_analysis.get("max_soc"),
            "batteries_count": battery_analysis.get("batteries_count"),
            "total_capacity": battery_analysis.get("total_capacity"),
            "batteries_full": battery_analysis.get("batteries_full"),
            "min_soc_threshold": battery_analysis.get("min_soc_threshold"),
            "max_soc_threshold": battery_analysis.get("max_soc_threshold"),
            "remaining_capacity_percent": battery_analysis.get("remaining_capacity_percent"),
        }


class PriceAnalysisSensor(ElectricityPlannerSensorBase):
    """Sensor for price analysis."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry) -> None:
        """Initialize the price analysis sensor."""
        super().__init__(coordinator, entry)
        self._attr_name = "Price Analysis"
        self._attr_unique_id = f"{entry.entry_id}_price_analysis"
        self._attr_icon = "mdi:currency-eur"
        self._attr_native_unit_of_measurement = "€/kWh"
        self._attr_device_class = SensorDeviceClass.MONETARY

    @property
    def native_value(self) -> float | None:
        """Return the current electricity price."""
        if not self.coordinator.data or "price_analysis" not in self.coordinator.data:
            return None
        
        return self.coordinator.data["price_analysis"].get("current_price")

    @property
    def extra_state_attributes(self) -> dict[str, any]:
        """Return the state attributes."""
        if not self.coordinator.data or "price_analysis" not in self.coordinator.data:
            return {}
        
        price_analysis = self.coordinator.data["price_analysis"]
        return {
            "highest_price": price_analysis.get("highest_price"),
            "lowest_price": price_analysis.get("lowest_price"),
            "next_price": price_analysis.get("next_price"),
            "price_threshold": price_analysis.get("price_threshold"),
            "is_low_price": price_analysis.get("is_low_price"),
            "is_lowest_price": price_analysis.get("is_lowest_price"),
            "price_position": price_analysis.get("price_position"),
            "next_price_higher": price_analysis.get("next_price_higher"),
            "price_trend_improving": price_analysis.get("price_trend_improving"),
            "very_low_price": price_analysis.get("very_low_price"),
        }


class PowerAnalysisSensor(ElectricityPlannerSensorBase):
    """Sensor for power flow analysis."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry) -> None:
        """Initialize the power analysis sensor.""" 
        super().__init__(coordinator, entry)
        self._attr_name = "Power Analysis"
        self._attr_unique_id = f"{entry.entry_id}_power_analysis"
        self._attr_icon = "mdi:transmission-tower"
        self._attr_native_unit_of_measurement = "W"
        self._attr_device_class = SensorDeviceClass.POWER

    @property
    def native_value(self) -> float | None:
        """Return the current house consumption."""
        if not self.coordinator.data or "power_analysis" not in self.coordinator.data:
            return None
        
        return self.coordinator.data["power_analysis"].get("house_consumption")

    @property
    def extra_state_attributes(self) -> dict[str, any]:
        """Return the state attributes."""
        if not self.coordinator.data or "power_analysis" not in self.coordinator.data:
            return {}
        
        power_analysis = self.coordinator.data["power_analysis"]
        return {
            "solar_surplus": power_analysis.get("solar_surplus"),
            "car_charging_power": power_analysis.get("car_charging_power"),
            "has_solar_surplus": power_analysis.get("has_solar_surplus"),
            "significant_solar_surplus": power_analysis.get("significant_solar_surplus"),
            "car_currently_charging": power_analysis.get("car_currently_charging"),
            "available_surplus_for_batteries": power_analysis.get("available_surplus_for_batteries"),
        }