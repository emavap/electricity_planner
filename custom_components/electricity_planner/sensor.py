"""Sensor platform for Electricity Planner."""
from __future__ import annotations

from datetime import datetime

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
        DataAvailabilitySensor(coordinator, entry),
        HourlyDecisionHistorySensor(coordinator, entry),
        ChargerLimitSensor(coordinator, entry),
        GridSetpointSensor(coordinator, entry),
        DecisionDiagnosticsSensor(coordinator, entry),
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
        self._attr_name = "Charging Recommendation & Reason"
        self._attr_unique_id = f"{entry.entry_id}_grid_charging_decision"
        self._attr_icon = "mdi:transmission-tower"

    @property
    def native_value(self) -> str:
        """Return the state of the sensor."""
        if not self.coordinator.data:
            return "no_data_available"
        
        battery_grid = self.coordinator.data.get("battery_grid_charging", False)
        car_grid = self.coordinator.data.get("car_grid_charging", False)
        
        if battery_grid and car_grid:
            return "charge_both_from_grid"
        elif battery_grid:
            battery_reason = self.coordinator.data.get("battery_grid_charging_reason", "")
            return f"charge_battery: {battery_reason}"
        elif car_grid:
            car_reason = self.coordinator.data.get("car_grid_charging_reason", "")
            return f"charge_car: {car_reason}"
        else:
            # Show the most relevant reason for not charging
            battery_reason = self.coordinator.data.get("battery_grid_charging_reason", "")
            return f"no_charging: {battery_reason}"

    @property
    def extra_state_attributes(self) -> dict[str, any]:
        """Return the state attributes."""
        if not self.coordinator.data:
            return {"data_available": False}
        
        price_data_available = self.coordinator.data.get("price_analysis", {}).get("data_available", False)
        
        return {
            "battery_grid_charging": self.coordinator.data.get("battery_grid_charging"),
            "car_grid_charging": self.coordinator.data.get("car_grid_charging"),
            "battery_grid_charging_reason": self.coordinator.data.get("battery_grid_charging_reason"),
            "car_grid_charging_reason": self.coordinator.data.get("car_grid_charging_reason"),
            "next_evaluation": self.coordinator.data.get("next_evaluation"),
            "data_available": True,
            "price_data_available": price_data_available,
        }


class BatteryAnalysisSensor(ElectricityPlannerSensorBase):
    """Sensor for battery analysis."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry) -> None:
        """Initialize the battery analysis sensor."""
        super().__init__(coordinator, entry)
        self._attr_name = "Battery SOC Average"
        self._attr_unique_id = f"{entry.entry_id}_battery_analysis"
        self._attr_icon = "mdi:battery"
        self._attr_device_class = SensorDeviceClass.BATTERY
        self._attr_native_unit_of_measurement = "%"

    @property
    def native_value(self) -> float | None:
        """Return the average battery SOC."""
        if not self.coordinator.data or "battery_analysis" not in self.coordinator.data:
            return 0  # Return 0 instead of None when no data
        
        avg_soc = self.coordinator.data["battery_analysis"].get("average_soc")
        return avg_soc if avg_soc is not None else 0  # Return 0 if no batteries configured

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
        self._attr_name = "Current Electricity Price"
        self._attr_unique_id = f"{entry.entry_id}_price_analysis"
        self._attr_icon = "mdi:currency-eur"
        self._attr_native_unit_of_measurement = "€/kWh"
        self._attr_device_class = SensorDeviceClass.MONETARY

    @property
    def native_value(self) -> float | None:
        """Return the current electricity price."""
        if not self.coordinator.data or "price_analysis" not in self.coordinator.data:
            return 0.0  # Return 0 instead of None when no data
        
        current_price = self.coordinator.data["price_analysis"].get("current_price")
        return current_price if current_price is not None else 0.0  # Return 0 if no price data

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
        self._attr_name = "Solar Surplus Power"
        self._attr_unique_id = f"{entry.entry_id}_power_analysis"
        self._attr_icon = "mdi:transmission-tower"
        self._attr_native_unit_of_measurement = "W"
        self._attr_device_class = SensorDeviceClass.POWER

    @property
    def native_value(self) -> float | None:
        """Return the solar surplus."""
        if not self.coordinator.data or "power_analysis" not in self.coordinator.data:
            return 0.0  # Return 0 instead of None when no data
        
        solar_surplus = self.coordinator.data["power_analysis"].get("solar_surplus")
        return solar_surplus if solar_surplus is not None else 0.0  # Return 0 if no power data

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


class DataAvailabilitySensor(ElectricityPlannerSensorBase):
    """Sensor for data availability duration tracking."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry) -> None:
        """Initialize the data availability sensor."""
        super().__init__(coordinator, entry)
        self._attr_name = "Data Unavailable Duration"
        self._attr_unique_id = f"{entry.entry_id}_data_unavailable_duration"
        self._attr_icon = "mdi:database-clock"
        self._attr_native_unit_of_measurement = "s"
        self._attr_device_class = SensorDeviceClass.DURATION

    @property
    def native_value(self) -> int | None:
        """Return the duration in seconds that data has been unavailable."""
        if not hasattr(self.coordinator, '_data_unavailable_since') or not self.coordinator._data_unavailable_since:
            return 0  # Data is available or never was unavailable
        
        unavailable_duration = datetime.now() - self.coordinator._data_unavailable_since
        return int(unavailable_duration.total_seconds())

    @property
    def extra_state_attributes(self) -> dict[str, any]:
        """Return the state attributes."""
        if not self.coordinator.data:
            return {}
        
        attributes = {}
        
        # Add availability information
        if hasattr(self.coordinator, '_last_successful_update'):
            attributes["last_successful_update"] = self.coordinator._last_successful_update.isoformat()
        
        if hasattr(self.coordinator, '_data_unavailable_since') and self.coordinator._data_unavailable_since:
            attributes["data_unavailable_since"] = self.coordinator._data_unavailable_since.isoformat()
        
        attributes.update({
            "notification_sent": getattr(self.coordinator, '_notification_sent', False),
            "data_currently_available": self.coordinator.data.get("price_analysis", {}).get("data_available", False),
        })
        
        return attributes


class HourlyDecisionHistorySensor(ElectricityPlannerSensorBase):
    """Sensor for hourly price and decision history data."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry) -> None:
        """Initialize the hourly decision history sensor."""
        super().__init__(coordinator, entry)
        self._attr_name = "Hourly Price & Decision History"
        self._attr_unique_id = f"{entry.entry_id}_hourly_decision_history"
        self._attr_icon = "mdi:chart-timeline-variant"
        self._attr_native_unit_of_measurement = "€/kWh"
        self._attr_device_class = SensorDeviceClass.MONETARY
        self._history_data = []
        self._last_hour_recorded = None

    @property
    def native_value(self) -> float | None:
        """Return the current electricity price for compatibility."""
        if not self.coordinator.data or "price_analysis" not in self.coordinator.data:
            return 0.0
        
        current_price = self.coordinator.data["price_analysis"].get("current_price")
        return current_price if current_price is not None else 0.0

    @property
    def extra_state_attributes(self) -> dict[str, any]:
        """Return the hourly history data as attributes."""
        if not self.coordinator.data:
            return self._cached_attributes if hasattr(self, '_cached_attributes') else {}
        
        # Check if price or decisions have changed since last update
        current_price = self.coordinator.data.get("price_analysis", {}).get("current_price")
        battery_charging = self.coordinator.data.get("battery_grid_charging", False)
        car_charging = self.coordinator.data.get("car_grid_charging", False)
        
        # Create state signature to detect changes
        current_state = (current_price, battery_charging, car_charging)
        
        # Only update when price or decisions change, or first time
        if not hasattr(self, '_last_state') or self._last_state != current_state:
            self._update_history()
            
            # Cache formatted data - only recalculate when data changes
            recent_data = self._history_data[-48:] if self._history_data else []
            self._cached_formatted_data = self._format_for_apex_charts(recent_data)
            self._cached_attributes = {
                "hourly_data": recent_data,
                "total_records": len(self._history_data),
                "last_updated": datetime.now().isoformat(),
                "price_data": self._cached_formatted_data.get("price_data", []),
                "battery_charging_data": self._cached_formatted_data.get("battery_charging_data", []),
                "car_charging_data": self._cached_formatted_data.get("car_charging_data", []),
            }
            self._last_state = current_state
        
        # Return cached attributes to avoid processing on every access
        return self._cached_attributes if hasattr(self, '_cached_attributes') else {}

    def _update_history(self):
        """Update the hourly history data."""
        if not self.coordinator.data:
            return

        current_hour = datetime.now().replace(minute=0, second=0, microsecond=0)
        
        # Only record once per hour
        if self._last_hour_recorded == current_hour:
            return

        price_analysis = self.coordinator.data.get("price_analysis", {})
        current_price = price_analysis.get("current_price")
        
        if current_price is None:
            return

        # Get decision data
        battery_charging = self.coordinator.data.get("battery_grid_charging", False)
        car_charging = self.coordinator.data.get("car_grid_charging", False)
        price_position = price_analysis.get("price_position", 0)
        is_low_price = price_analysis.get("very_low_price", False)

        # Create hourly record
        hourly_record = {
            "timestamp": current_hour.isoformat(),
            "hour": current_hour.hour,
            "price": round(current_price, 4),
            "battery_charging": battery_charging,
            "car_charging": car_charging,
            "price_position": round(price_position, 2) if price_position else 0,
            "is_low_price": is_low_price,
            "charging_decision": "both" if battery_charging and car_charging else "battery" if battery_charging else "car" if car_charging else "none",
        }

        # Add to history
        self._history_data.append(hourly_record)
        self._last_hour_recorded = current_hour

        # Keep only last 7 days (168 hours)
        if len(self._history_data) > 168:
            self._history_data = self._history_data[-168:]

    def _format_for_apex_charts(self, data):
        """Format data specifically for ApexCharts integration."""
        if not data:
            return {}

        price_series = []
        battery_series = []
        car_series = []
        
        for record in data:
            timestamp = record["timestamp"]
            price_series.append([timestamp, record["price"]])
            battery_series.append([timestamp, 1 if record["battery_charging"] else 0])
            car_series.append([timestamp, 1 if record["car_charging"] else 0])
        
        return {
            "price_data": price_series,
            "battery_charging_data": battery_series,
            "car_charging_data": car_series,
        }


class ChargerLimitSensor(ElectricityPlannerSensorBase):
    """Sensor for optimal car charger power limit."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry) -> None:
        """Initialize the charger limit sensor."""
        super().__init__(coordinator, entry)
        self._attr_name = "Car Charger Limit"
        self._attr_unique_id = f"{entry.entry_id}_charger_limit"
        self._attr_icon = "mdi:ev-station"
        self._attr_native_unit_of_measurement = "W"
        self._attr_device_class = SensorDeviceClass.POWER

    @property
    def native_value(self) -> int:
        """Return the recommended charger power limit."""
        if not self.coordinator.data:
            return 0
        
        return self.coordinator.data.get("charger_limit", 0)

    @property
    def extra_state_attributes(self) -> dict[str, any]:
        """Return the state attributes."""
        if not self.coordinator.data:
            return {}
        
        return {
            "charger_limit_reason": self.coordinator.data.get("charger_limit_reason", ""),
            "current_car_power": self.coordinator.data.get("power_analysis", {}).get("car_charging_power", 0),
            "solar_surplus": self.coordinator.data.get("power_analysis", {}).get("solar_surplus", 0),
            "car_currently_charging": self.coordinator.data.get("power_analysis", {}).get("car_currently_charging", False),
        }


class GridSetpointSensor(ElectricityPlannerSensorBase):
    """Sensor for grid setpoint to avoid battery discharge during car charging."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry) -> None:
        """Initialize the grid setpoint sensor."""
        super().__init__(coordinator, entry)
        self._attr_name = "Grid Setpoint"
        self._attr_unique_id = f"{entry.entry_id}_grid_setpoint"
        self._attr_icon = "mdi:transmission-tower"
        self._attr_native_unit_of_measurement = "W"
        self._attr_device_class = SensorDeviceClass.POWER

    @property
    def native_value(self) -> int:
        """Return the recommended grid setpoint."""
        if not self.coordinator.data:
            return 0
        
        return self.coordinator.data.get("grid_setpoint", 0)

    @property
    def extra_state_attributes(self) -> dict[str, any]:
        """Return the state attributes."""
        if not self.coordinator.data:
            return {}
        
        monthly_peak = self.coordinator.data.get("monthly_grid_peak", 0)
        max_grid_setpoint = max(monthly_peak, 2500) if monthly_peak and monthly_peak > 2500 else 2500
        
        return {
            "grid_setpoint_reason": self.coordinator.data.get("grid_setpoint_reason", ""),
            "charger_limit": self.coordinator.data.get("charger_limit", 0),
            "current_car_power": self.coordinator.data.get("power_analysis", {}).get("car_charging_power", 0),
            "solar_surplus": self.coordinator.data.get("power_analysis", {}).get("solar_surplus", 0),
            "battery_average_soc": self.coordinator.data.get("battery_analysis", {}).get("average_soc", 0),
            "monthly_grid_peak": monthly_peak,
            "max_grid_setpoint": max_grid_setpoint,
        }


class DecisionDiagnosticsSensor(ElectricityPlannerSensorBase):
    """Comprehensive diagnostics sensor exposing all decision parameters for validation."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry) -> None:
        """Initialize the decision diagnostics sensor."""
        super().__init__(coordinator, entry)
        self._attr_name = "Decision Diagnostics"
        self._attr_unique_id = f"{entry.entry_id}_decision_diagnostics"
        self._attr_icon = "mdi:bug-check"
        self._attr_native_unit_of_measurement = None

    @property
    def native_value(self) -> str:
        """Return a summary of the decision state."""
        if not self.coordinator.data:
            return "no_data"
        
        battery_charging = self.coordinator.data.get("battery_grid_charging", False)
        car_charging = self.coordinator.data.get("car_grid_charging", False)
        
        if battery_charging and car_charging:
            return "charging_both"
        elif battery_charging:
            return "charging_battery"
        elif car_charging:
            return "charging_car"
        else:
            return "no_charging"

    @property
    def extra_state_attributes(self) -> dict[str, any]:
        """Return comprehensive diagnostics data for decision validation."""
        if not self.coordinator.data:
            return {"error": "No coordinator data available"}
        
        # Get all analysis data
        price_analysis = self.coordinator.data.get("price_analysis", {})
        battery_analysis = self.coordinator.data.get("battery_analysis", {})
        power_analysis = self.coordinator.data.get("power_analysis", {})
        power_allocation = self.coordinator.data.get("power_allocation", {})
        solar_analysis = self.coordinator.data.get("solar_analysis", {})
        solar_forecast = self.coordinator.data.get("solar_forecast", {})
        time_context = self.coordinator.data.get("time_context", {})
        
        # Configuration values (for validation)
        config = self.coordinator.config
        
        return {
            # Main decisions
            "decisions": {
                "battery_grid_charging": self.coordinator.data.get("battery_grid_charging", False),
                "car_grid_charging": self.coordinator.data.get("car_grid_charging", False),
                "feedin_solar": self.coordinator.data.get("feedin_solar", False),
                "battery_reason": self.coordinator.data.get("battery_grid_charging_reason", ""),
                "car_reason": self.coordinator.data.get("car_grid_charging_reason", ""),
                "feedin_reason": self.coordinator.data.get("feedin_solar_reason", ""),
            },
            
            # Power outputs
            "power_outputs": {
                "charger_limit": self.coordinator.data.get("charger_limit", 0),
                "grid_setpoint": self.coordinator.data.get("grid_setpoint", 0),
                "charger_limit_reason": self.coordinator.data.get("charger_limit_reason", ""),
                "grid_setpoint_reason": self.coordinator.data.get("grid_setpoint_reason", ""),
            },
            
            # Price analysis (for validation)
            "price_analysis": {
                "current_price": price_analysis.get("current_price"),
                "highest_price": price_analysis.get("highest_price"),
                "lowest_price": price_analysis.get("lowest_price"),
                "next_price": price_analysis.get("next_price"),
                "price_threshold": price_analysis.get("price_threshold"),
                "is_low_price": price_analysis.get("is_low_price", False),
                "very_low_price": price_analysis.get("very_low_price", False),
                "price_position": price_analysis.get("price_position"),
                "significant_price_drop": price_analysis.get("significant_price_drop", False),
                "data_available": price_analysis.get("data_available", False),
            },
            
            # Battery analysis (for validation)
            "battery_analysis": {
                "average_soc": battery_analysis.get("average_soc"),
                "min_soc": battery_analysis.get("min_soc"),
                "max_soc": battery_analysis.get("max_soc"),
                "batteries_count": battery_analysis.get("batteries_count", 0),
                "batteries_full": battery_analysis.get("batteries_full", False),
                "batteries_available": battery_analysis.get("batteries_available", True),
                "min_soc_threshold": battery_analysis.get("min_soc_threshold"),
                "max_soc_threshold": battery_analysis.get("max_soc_threshold"),
            },
            
            # Power analysis (for validation)
            "power_analysis": {
                "solar_surplus": power_analysis.get("solar_surplus"),
                "car_charging_power": power_analysis.get("car_charging_power"),
                "has_solar_surplus": power_analysis.get("has_solar_surplus", False),
                "significant_solar_surplus": power_analysis.get("significant_solar_surplus", False),
                "car_currently_charging": power_analysis.get("car_currently_charging", False),
            },
            
            # Power allocation (critical for validation)
            "power_allocation": {
                "solar_for_batteries": power_allocation.get("solar_for_batteries", 0),
                "solar_for_car": power_allocation.get("solar_for_car", 0),
                "car_current_solar_usage": power_allocation.get("car_current_solar_usage", 0),
                "remaining_solar": power_allocation.get("remaining_solar", 0),
                "total_allocated": power_allocation.get("total_allocated", 0),
                "allocation_reason": power_allocation.get("allocation_reason", ""),
            },
            
            # Solar forecast (for validation)
            "solar_forecast": {
                "forecast_available": solar_forecast.get("forecast_available", False),
                "solar_production_factor": solar_forecast.get("solar_production_factor"),
                "expected_solar_production": solar_forecast.get("expected_solar_production", "unknown"),
                "sunny_hours": solar_forecast.get("sunny_hours", 0),
                "cloudy_hours": solar_forecast.get("cloudy_hours", 0),
            },
            
            # Time context (for validation)
            "time_context": {
                "current_hour": time_context.get("current_hour"),
                "is_night": time_context.get("is_night", False),
                "is_early_morning": time_context.get("is_early_morning", False),
                "is_solar_peak": time_context.get("is_solar_peak", False),
                "is_evening": time_context.get("is_evening", False),
                "winter_season": time_context.get("winter_season", False),
            },
            
            # Configuration values (for validation)
            "configured_limits": {
                "emergency_soc": config.get("emergency_soc_threshold", 15),
                "max_battery_power": config.get("max_battery_power", 3000),
                "max_car_power": config.get("max_car_power", 11000),
                "max_grid_power": config.get("max_grid_power", 15000),
                "min_car_charging_threshold": config.get("min_car_charging_threshold", 100),
                "emergency_soc_override": config.get("emergency_soc_override", 25),
                "winter_night_soc_override": config.get("winter_night_soc_override", 40),
                "solar_peak_emergency_soc": config.get("solar_peak_emergency_soc", 25),
                "predictive_charging_min_soc": config.get("predictive_charging_min_soc", 30),
                "significant_solar_threshold": config.get("significant_solar_threshold", 1000),
                "very_low_price_threshold": config.get("very_low_price_threshold", 30),
                "price_threshold": config.get("price_threshold", 0.15),
                "feedin_price_threshold": config.get("feedin_price_threshold", 0.05),
            },
            
            # Validation flags (for quick problem identification)
            "validation_flags": {
                "price_data_valid": price_analysis.get("data_available", False),
                "battery_data_valid": battery_analysis.get("batteries_available", True),
                "power_allocation_valid": power_allocation.get("total_allocated", 0) <= power_analysis.get("solar_surplus", 0),
                "emergency_override_active": self._check_emergency_override(battery_analysis, time_context, config),
                "predictive_logic_active": self._check_predictive_logic(price_analysis, battery_analysis, config),
                "solar_forecast_influencing": solar_forecast.get("forecast_available", False) and solar_forecast.get("solar_production_factor", 0.5) != 0.5,
            },
            
            # Last update
            "last_evaluation": self.coordinator.data.get("next_evaluation", "unknown"),
        }

    def _check_emergency_override(self, battery_analysis: dict, time_context: dict, config: dict) -> bool:
        """Check if any emergency overrides are currently active."""
        average_soc = battery_analysis.get("average_soc", 100)
        if average_soc is None:
            return False
            
        emergency_soc = config.get("emergency_soc_threshold", 15)
        emergency_soc_override = config.get("emergency_soc_override", 25)
        winter_night_soc_override = config.get("winter_night_soc_override", 40)
        solar_peak_emergency_soc = config.get("solar_peak_emergency_soc", 25)
        
        is_night = time_context.get("is_night", False)
        winter_season = time_context.get("winter_season", False)
        is_solar_peak = time_context.get("is_solar_peak", False)
        
        return (
            average_soc < emergency_soc or
            average_soc < emergency_soc_override or
            (is_night and winter_season and average_soc < winter_night_soc_override) or
            (is_solar_peak and average_soc < solar_peak_emergency_soc)
        )

    def _check_predictive_logic(self, price_analysis: dict, battery_analysis: dict, config: dict) -> bool:
        """Check if predictive charging logic is active."""
        significant_price_drop = price_analysis.get("significant_price_drop", False)
        is_low_price = price_analysis.get("is_low_price", False)
        average_soc = battery_analysis.get("average_soc", 100)
        predictive_min_soc = config.get("predictive_charging_min_soc", 30)
        
        if average_soc is None:
            return False
            
        return is_low_price and significant_price_drop and average_soc > predictive_min_soc