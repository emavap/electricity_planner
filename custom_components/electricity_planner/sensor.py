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
        SolarAnalysisSensor(coordinator, entry),
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
    """Sensor for charging decision status."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry) -> None:
        """Initialize the charging decision sensor."""
        super().__init__(coordinator, entry)
        self._attr_name = "Charging Decision"
        self._attr_unique_id = f"{entry.entry_id}_charging_decision"
        self._attr_icon = "mdi:battery-charging"

    @property
    def native_value(self) -> str:
        """Return the state of the sensor."""
        if not self.coordinator.data:
            return "unknown"
        
        battery_recommended = self.coordinator.data.get("battery_charging_recommended", False)
        car_recommended = self.coordinator.data.get("car_charging_recommended", False)
        
        if battery_recommended and car_recommended:
            return "charge_both"
        elif battery_recommended:
            return "charge_battery"
        elif car_recommended:
            return "charge_car"
        else:
            return "wait"

    @property
    def extra_state_attributes(self) -> dict[str, any]:
        """Return the state attributes."""
        if not self.coordinator.data:
            return {}
        
        return {
            "battery_charging_recommended": self.coordinator.data.get("battery_charging_recommended"),
            "car_charging_recommended": self.coordinator.data.get("car_charging_recommended"),
            "battery_charging_reason": self.coordinator.data.get("battery_charging_reason"),
            "car_charging_reason": self.coordinator.data.get("car_charging_reason"),
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
            "needs_charging": battery_analysis.get("needs_charging"),
            "batteries_full": battery_analysis.get("batteries_full"),
            "min_soc_threshold": battery_analysis.get("min_soc_threshold"),
            "max_soc_threshold": battery_analysis.get("max_soc_threshold"),
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
            "price_threshold": price_analysis.get("price_threshold"),
            "is_low_price": price_analysis.get("is_low_price"),
            "price_ratio": price_analysis.get("price_ratio"),
            "recommendation": price_analysis.get("recommendation"),
        }


class SolarAnalysisSensor(ElectricityPlannerSensorBase):
    """Sensor for solar analysis."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry) -> None:
        """Initialize the solar analysis sensor.""" 
        super().__init__(coordinator, entry)
        self._attr_name = "Solar Analysis"
        self._attr_unique_id = f"{entry.entry_id}_solar_analysis"
        self._attr_icon = "mdi:solar-power"
        self._attr_native_unit_of_measurement = "kW"
        self._attr_device_class = SensorDeviceClass.POWER

    @property
    def native_value(self) -> float | None:
        """Return the current solar production."""
        if not self.coordinator.data or "solar_analysis" not in self.coordinator.data:
            return None
        
        return self.coordinator.data["solar_analysis"].get("current_production")

    @property
    def extra_state_attributes(self) -> dict[str, any]:
        """Return the state attributes."""
        if not self.coordinator.data or "solar_analysis" not in self.coordinator.data:
            return {}
        
        solar_analysis = self.coordinator.data["solar_analysis"]
        return {
            "forecast": solar_analysis.get("forecast"),
            "forecast_hours": solar_analysis.get("forecast_hours"),
            "is_producing": solar_analysis.get("is_producing"),
            "has_good_forecast": solar_analysis.get("has_good_forecast"),
            "recommendation": solar_analysis.get("recommendation"),
        }