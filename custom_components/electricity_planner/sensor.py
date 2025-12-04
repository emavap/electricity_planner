"""Sensor platform for Electricity Planner."""
from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    PHASE_MODE_SINGLE,
    CONF_PRICE_THRESHOLD,
    CONF_FEEDIN_PRICE_THRESHOLD,
    CONF_FEEDIN_ADJUSTMENT_MULTIPLIER,
    CONF_FEEDIN_ADJUSTMENT_OFFSET,
    CONF_PRICE_ADJUSTMENT_MULTIPLIER,
    CONF_PRICE_ADJUSTMENT_OFFSET,
    CONF_TRANSPORT_COST_ENTITY,
    CONF_VERY_LOW_PRICE_THRESHOLD,
    CONF_SIGNIFICANT_SOLAR_THRESHOLD,
    CONF_EMERGENCY_SOC_THRESHOLD,
    DEFAULT_PRICE_THRESHOLD,
    DEFAULT_FEEDIN_PRICE_THRESHOLD,
    DEFAULT_FEEDIN_ADJUSTMENT_MULTIPLIER,
    DEFAULT_FEEDIN_ADJUSTMENT_OFFSET,
    DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER,
    DEFAULT_PRICE_ADJUSTMENT_OFFSET,
    DEFAULT_VERY_LOW_PRICE_THRESHOLD,
    DEFAULT_SIGNIFICANT_SOLAR_THRESHOLD,
    DEFAULT_EMERGENCY_SOC,
    INTEGRATION_VERSION,
)
from .coordinator import ElectricityPlannerCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    # AUTOMATION SENSORS: Essential power sensors for automations (2/5 total automation sensors)
    automation_entities = [
        ChargerLimitSensor(coordinator, entry, "_automation"),
        GridSetpointSensor(coordinator, entry, "_automation"),
    ]

    # DIAGNOSTIC SENSORS: For monitoring and troubleshooting only
    diagnostic_entities = [
        ChargingDecisionSensor(coordinator, entry, "_diagnostic"),
        BatteryAnalysisSensor(coordinator, entry, "_diagnostic"),
        PriceAnalysisSensor(coordinator, entry, "_diagnostic"),
        PowerAnalysisSensor(coordinator, entry, "_diagnostic"),
        DataAvailabilitySensor(coordinator, entry, "_diagnostic"),
        DecisionDiagnosticsSensor(coordinator, entry, "_diagnostic"),
        ForecastInsightsSensor(coordinator, entry, "_diagnostic"),
        PriceThresholdSensor(coordinator, entry, "_diagnostic"),
        FeedinPriceThresholdSensor(coordinator, entry, "_diagnostic"),
        BuyPriceMarginSensor(coordinator, entry, "_diagnostic"),
        FeedinPriceMarginSensor(coordinator, entry, "_diagnostic"),
        FeedinPriceSensor(coordinator, entry, "_diagnostic"),
        VeryLowPriceThresholdSensor(coordinator, entry, "_diagnostic"),
        SignificantSolarThresholdSensor(coordinator, entry, "_diagnostic"),
        EmergencySOCThresholdSensor(coordinator, entry, "_diagnostic"),
        NordPoolPricesSensor(coordinator, entry, "_diagnostic"),
    ]

    entities = automation_entities + diagnostic_entities

    async_add_entities(entities, False)


class ElectricityPlannerSensorBase(CoordinatorEntity, SensorEntity):
    """Base class for Electricity Planner sensors."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry, device_suffix: str = "") -> None:
        """Initialize the sensor."""
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
            "manufacturer": "Electricity Planner",
            "model": "Electricity Planner",
            "sw_version": INTEGRATION_VERSION,
        }


class ChargingDecisionSensor(ElectricityPlannerSensorBase):
    """Sensor for grid charging decision status."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry, device_suffix: str = "") -> None:
        """Initialize the charging decision sensor."""
        super().__init__(coordinator, entry, device_suffix)
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
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        if not self.coordinator.data:
            return {"data_available": False}

        price_data_available = self.coordinator.data.get("price_analysis", {}).get("data_available", False)

        attributes = {
            "battery_grid_charging": self.coordinator.data.get("battery_grid_charging"),
            "car_grid_charging": self.coordinator.data.get("car_grid_charging"),
            "battery_grid_charging_reason": self.coordinator.data.get("battery_grid_charging_reason"),
            "car_grid_charging_reason": self.coordinator.data.get("car_grid_charging_reason"),
            "next_evaluation": self.coordinator.data.get("next_evaluation"),
            "data_available": True,
            "price_data_available": price_data_available,
            "phase_mode": self.coordinator.data.get("phase_mode", PHASE_MODE_SINGLE),
        }

        phase_results = self.coordinator.data.get("phase_results") or {}
        if phase_results:
            attributes["phase_results"] = phase_results

        return attributes


class BatteryAnalysisSensor(ElectricityPlannerSensorBase):
    """Sensor for battery analysis."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry, device_suffix: str = "") -> None:
        """Initialize the battery analysis sensor."""
        super().__init__(coordinator, entry, device_suffix)
        self._attr_name = "Battery SOC Average"
        self._attr_unique_id = f"{entry.entry_id}_battery_analysis"
        self._attr_icon = "mdi:battery"
        self._attr_device_class = SensorDeviceClass.BATTERY
        self._attr_native_unit_of_measurement = "%"

    @property
    def native_value(self) -> float | None:
        """Return the average battery SOC."""
        if not self.coordinator.data or "battery_analysis" not in self.coordinator.data:
            return None

        avg_soc = self.coordinator.data["battery_analysis"].get("average_soc")
        return avg_soc

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
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

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry, device_suffix: str = "") -> None:
        """Initialize the price analysis sensor."""
        super().__init__(coordinator, entry, device_suffix)
        self._attr_name = "Current Electricity Price"
        self._attr_unique_id = f"{entry.entry_id}_price_analysis"
        self._attr_icon = "mdi:currency-eur"
        self._attr_native_unit_of_measurement = "€/kWh"
        self._attr_device_class = SensorDeviceClass.MONETARY

    @property
    def native_value(self) -> float | None:
        """Return the current electricity price."""
        if not self.coordinator.data or "price_analysis" not in self.coordinator.data:
            return None

        current_price = self.coordinator.data["price_analysis"].get("current_price")
        return current_price

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        if not self.coordinator.data or "price_analysis" not in self.coordinator.data:
            return {}

        price_analysis = self.coordinator.data["price_analysis"]
        return {
            "highest_price": price_analysis.get("highest_price"),
            "lowest_price": price_analysis.get("lowest_price"),
            "next_price": price_analysis.get("next_price"),
            "raw_current_price": price_analysis.get("raw_current_price"),
            "raw_highest_price": price_analysis.get("raw_highest_price"),
            "raw_lowest_price": price_analysis.get("raw_lowest_price"),
            "raw_next_price": price_analysis.get("raw_next_price"),
            "price_adjustment_multiplier": price_analysis.get("price_adjustment_multiplier"),
            "price_adjustment_offset": price_analysis.get("price_adjustment_offset"),
            "transport_cost": price_analysis.get("transport_cost"),
            "price_threshold": price_analysis.get("price_threshold"),
            "dynamic_threshold": price_analysis.get("dynamic_threshold"),
            "is_low_price": price_analysis.get("is_low_price"),
            "is_lowest_price": price_analysis.get("is_lowest_price"),
            "price_position": price_analysis.get("price_position"),
            "next_price_higher": price_analysis.get("next_price_higher"),
            "price_trend_improving": price_analysis.get("price_trend_improving"),
            "very_low_price": price_analysis.get("very_low_price"),
        }


class PowerAnalysisSensor(ElectricityPlannerSensorBase):
    """Sensor for power flow analysis."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry, device_suffix: str = "") -> None:
        """Initialize the power analysis sensor."""
        super().__init__(coordinator, entry, device_suffix)
        self._attr_name = "Solar Surplus Power"
        self._attr_unique_id = f"{entry.entry_id}_power_analysis"
        self._attr_icon = "mdi:transmission-tower"
        self._attr_native_unit_of_measurement = "W"
        self._attr_device_class = SensorDeviceClass.POWER

    @property
    def native_value(self) -> float | None:
        """Return the solar surplus."""
        if not self.coordinator.data or "power_analysis" not in self.coordinator.data:
            return None

        return self.coordinator.data["power_analysis"].get("solar_surplus")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        if not self.coordinator.data or "power_analysis" not in self.coordinator.data:
            return {}

        power_analysis = self.coordinator.data["power_analysis"]
        attributes = {
            "solar_production": power_analysis.get("solar_production"),
            "house_consumption": power_analysis.get("house_consumption"),
            "house_consumption_without_car": power_analysis.get("house_consumption_without_car"),
            "solar_surplus": power_analysis.get("solar_surplus"),  # Available excess for batteries/car/export
            "car_charging_power": power_analysis.get("car_charging_power"),
            "has_solar_production": power_analysis.get("has_solar_production"),
            "has_solar_surplus": power_analysis.get("has_solar_surplus"),  # Has available excess solar
            "significant_solar_surplus": power_analysis.get("significant_solar_surplus"),
            "car_currently_charging": power_analysis.get("car_currently_charging"),
            "available_surplus_for_batteries": power_analysis.get("available_surplus_for_batteries"),
            "solar_coverage_ratio": power_analysis.get("solar_coverage_ratio"),
            "has_excess_solar_available": power_analysis.get("has_excess_solar_available"),  # Available for any use
        }

        # Add grid power snapshot (if configured)
        grid_power = self.coordinator.data.get("grid_power")
        if grid_power is not None:
            attributes["grid_power"] = grid_power
            attributes["grid_import"] = abs(min(0, grid_power))  # Positive value for import
            attributes["grid_export"] = max(0, grid_power)  # Positive value for export

        # Expose peak import limiting status
        attributes["car_peak_limited"] = bool(self.coordinator.data.get("car_peak_limited", False))
        peak_threshold = self.coordinator.data.get("car_peak_limit_threshold")
        if peak_threshold is not None:
            attributes["car_peak_limit_threshold_w"] = peak_threshold

        return attributes


class DataAvailabilitySensor(ElectricityPlannerSensorBase):
    """Sensor for data availability duration tracking."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry, device_suffix: str = "") -> None:
        """Initialize the data availability sensor."""
        super().__init__(coordinator, entry, device_suffix)
        self._attr_name = "Data Unavailable Duration"
        self._attr_unique_id = f"{entry.entry_id}_data_unavailable_duration"
        self._attr_icon = "mdi:database-clock"
        self._attr_native_unit_of_measurement = "s"
        self._attr_device_class = SensorDeviceClass.DURATION

    @property
    def native_value(self) -> int | None:
        """Return the duration in seconds that data has been unavailable."""
        if not self.coordinator.data_unavailable_since:
            return 0  # Data is available or never was unavailable

        unavailable_duration = dt_util.utcnow() - self.coordinator.data_unavailable_since
        return int(unavailable_duration.total_seconds())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        if not self.coordinator.data:
            return {}

        attributes = {}

        # Add availability information
        if self.coordinator.last_successful_update:
            attributes["last_successful_update"] = self.coordinator.last_successful_update.isoformat()

        if self.coordinator.data_unavailable_since:
            attributes["data_unavailable_since"] = self.coordinator.data_unavailable_since.isoformat()

        attributes.update({
            "notification_sent": self.coordinator.notification_sent,
            "data_currently_available": self.coordinator.is_data_available(),
        })

        return attributes


class ChargerLimitSensor(ElectricityPlannerSensorBase):
    """AUTOMATION SENSOR (4/5): Car charger power limit in Watts."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry, device_suffix: str = "") -> None:
        """Initialize the charger limit sensor."""
        super().__init__(coordinator, entry, device_suffix)
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
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        if not self.coordinator.data:
            return {}

        attributes = {
            "charger_limit_reason": self.coordinator.data.get("charger_limit_reason", ""),
            "current_car_power": self.coordinator.data.get("power_analysis", {}).get("car_charging_power", 0),
            "solar_surplus": self.coordinator.data.get("power_analysis", {}).get("solar_surplus", 0),
            "car_currently_charging": self.coordinator.data.get("power_analysis", {}).get("car_currently_charging", False),
        }

        # Add override status
        manual_overrides = self.coordinator.data.get("manual_overrides", {})
        if "charger_limit" in manual_overrides:
            override_info = manual_overrides["charger_limit"]
            attributes["is_overridden"] = True
            attributes["override_value"] = override_info.get("value")
            attributes["override_reason"] = override_info.get("reason")
            attributes["override_expires_at"] = override_info.get("expires_at")
        else:
            attributes["is_overridden"] = False

        return attributes


class GridSetpointSensor(ElectricityPlannerSensorBase):
    """AUTOMATION SENSOR (5/5): Grid power setpoint in Watts."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry, device_suffix: str = "") -> None:
        """Initialize the grid setpoint sensor."""
        super().__init__(coordinator, entry, device_suffix)
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
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        if not self.coordinator.data:
            return {}

        monthly_peak = self.coordinator.data.get("monthly_grid_peak", 0)
        max_grid_setpoint = max(monthly_peak, 2500) if monthly_peak and monthly_peak > 2500 else 2500

        attributes = {
            "grid_setpoint_reason": self.coordinator.data.get("grid_setpoint_reason", ""),
            "charger_limit": self.coordinator.data.get("charger_limit", 0),
            "current_car_power": self.coordinator.data.get("power_analysis", {}).get("car_charging_power", 0),
            "solar_surplus": self.coordinator.data.get("power_analysis", {}).get("solar_surplus", 0),
            "battery_average_soc": self.coordinator.data.get("battery_analysis", {}).get("average_soc", 0),
            "monthly_grid_peak": monthly_peak,
            "max_grid_setpoint": max_grid_setpoint,
        }

        attributes["phase_mode"] = self.coordinator.data.get("phase_mode", PHASE_MODE_SINGLE)

        phase_results = self.coordinator.data.get("phase_results") or {}
        if phase_results:
            attributes["phase_results"] = phase_results
            attributes["phase_grid_setpoints"] = {
                phase: result.get("grid_setpoint")
                for phase, result in phase_results.items()
            }
            attributes["phase_grid_components"] = {
                phase: result.get("grid_components")
                for phase, result in phase_results.items()
            }

        # Add override status
        manual_overrides = self.coordinator.data.get("manual_overrides", {})
        if "grid_setpoint" in manual_overrides:
            override_info = manual_overrides["grid_setpoint"]
            attributes["is_overridden"] = True
            attributes["override_value"] = override_info.get("value")
            attributes["override_reason"] = override_info.get("reason")
            attributes["override_expires_at"] = override_info.get("expires_at")
        else:
            attributes["is_overridden"] = False

        return attributes


class DecisionDiagnosticsSensor(ElectricityPlannerSensorBase):
    """Comprehensive diagnostics sensor exposing all decision parameters for validation."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry, device_suffix: str = "") -> None:
        """Initialize the decision diagnostics sensor."""
        super().__init__(coordinator, entry, device_suffix)
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
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return comprehensive diagnostics data for decision validation."""
        if not self.coordinator.data:
            return {"error": "No coordinator data available"}

        # Get all analysis data
        price_analysis = self.coordinator.data.get("price_analysis", {})
        battery_analysis = self.coordinator.data.get("battery_analysis", {})
        power_analysis = self.coordinator.data.get("power_analysis", {})
        power_allocation = self.coordinator.data.get("power_allocation", {})
        solar_analysis = self.coordinator.data.get("solar_analysis", {})
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
                "manual_overrides": self.coordinator.data.get("manual_overrides", {}),
            },

            # Strategy evaluation order
            "strategy_trace": self.coordinator.data.get("strategy_trace", []),
            "forecast_summary": self.coordinator.data.get("forecast_summary", {}),

            # Power outputs
            "power_outputs": {
                "charger_limit": self.coordinator.data.get("charger_limit", 0),
                "grid_setpoint": self.coordinator.data.get("grid_setpoint", 0),
                "charger_limit_reason": self.coordinator.data.get("charger_limit_reason", ""),
                "grid_setpoint_reason": self.coordinator.data.get("grid_setpoint_reason", ""),
                "grid_components": self.coordinator.data.get("grid_components", {}),
                "phase_mode": self.coordinator.data.get("phase_mode", PHASE_MODE_SINGLE),
                "phase_results": self.coordinator.data.get("phase_results", {}),
            },
            "phase_details": self.coordinator.data.get("phase_details", {}),
            "phase_capacity_map": self.coordinator.data.get("phase_capacity_map", {}),
            "phase_batteries": self.coordinator.data.get("phase_batteries", {}),

            # Price analysis (for validation)
            "price_analysis": {
                "current_price": price_analysis.get("current_price"),
                "highest_price": price_analysis.get("highest_price"),
                "lowest_price": price_analysis.get("lowest_price"),
                "next_price": price_analysis.get("next_price"),
                "raw_current_price": price_analysis.get("raw_current_price"),
                "raw_highest_price": price_analysis.get("raw_highest_price"),
                "raw_lowest_price": price_analysis.get("raw_lowest_price"),
                "raw_next_price": price_analysis.get("raw_next_price"),
                "price_adjustment_multiplier": price_analysis.get("price_adjustment_multiplier"),
                "price_adjustment_offset": price_analysis.get("price_adjustment_offset"),
                "price_threshold": price_analysis.get("price_threshold"),
                "average_threshold": self.coordinator.data.get("average_threshold"),
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
                "min_car_charging_duration": config.get("min_car_charging_duration", 2),
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
            },

            # Last update
            "last_evaluation": self.coordinator.data.get("next_evaluation", "unknown"),
        }

    def _check_emergency_override(self, battery_analysis: dict, time_context: dict, config: dict) -> bool:
        """Check if any emergency overrides are currently active."""
        average_soc = battery_analysis.get("average_soc")
        if average_soc is None:
            return False

        emergency_soc = config.get("emergency_soc_threshold", 15)
        solar_peak_emergency_soc = config.get("solar_peak_emergency_soc", 25)
        is_solar_peak = time_context.get("is_solar_peak", False)

        return (
            average_soc < emergency_soc or
            (is_solar_peak and average_soc < solar_peak_emergency_soc)
        )

    def _check_predictive_logic(self, price_analysis: dict, battery_analysis: dict, config: dict) -> bool:
        """Check if predictive charging logic is active."""
        significant_price_drop = price_analysis.get("significant_price_drop", False)
        is_low_price = price_analysis.get("is_low_price", False)
        average_soc = battery_analysis.get("average_soc")
        predictive_min_soc = config.get("predictive_charging_min_soc", 30)

        if average_soc is None:
            return False

        return is_low_price and significant_price_drop and average_soc > predictive_min_soc


class ForecastInsightsSensor(ElectricityPlannerSensorBase):
    """Sensor exposing upcoming price forecast insights."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry, device_suffix: str = "") -> None:
        """Initialize the forecast sensor."""
        super().__init__(coordinator, entry, device_suffix)
        self._attr_name = "Price Forecast Insights"
        self._attr_unique_id = f"{entry.entry_id}_forecast_insights"
        self._attr_icon = "mdi:chart-line"
        self._attr_native_unit_of_measurement = "€/kWh"

    @property
    def native_value(self) -> float | None:
        """Return the cheapest upcoming interval price."""
        if not self.coordinator.data:
            return None
        summary = self.coordinator.data.get("forecast_summary")
        if not summary or not summary.get("available"):
            return None
        return summary.get("cheapest_interval_price")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return detailed forecast data."""
        if not self.coordinator.data:
            return {}
        summary = self.coordinator.data.get("forecast_summary")
        if not summary:
            return {}
        return summary.copy()


class PriceThresholdSensor(ElectricityPlannerSensorBase):
    """Sensor displaying the configured price threshold for charging decisions."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry, device_suffix: str = "") -> None:
        """Initialize the price threshold sensor."""
        super().__init__(coordinator, entry, device_suffix)
        self._attr_name = "Price Threshold"
        self._attr_unique_id = f"{entry.entry_id}_price_threshold"
        self._attr_icon = "mdi:currency-eur"
        self._attr_device_class = None
        self._attr_unit_of_measurement = "€/kWh"

    @property
    def native_value(self) -> float:
        """Return the configured price threshold."""
        return self.coordinator.config.get(CONF_PRICE_THRESHOLD, DEFAULT_PRICE_THRESHOLD)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        if not self.coordinator.data:
            return {}

        current_price = self.coordinator.data.get("price_analysis", {}).get("current_price")
        threshold = self.native_value

        return {
            "description": "Price below which grid charging is considered favorable",
            "current_price": current_price,
            "price_below_threshold": current_price < threshold if current_price else None,
            "margin": round(current_price - threshold, 3) if current_price else None,
        }


class FeedinPriceThresholdSensor(ElectricityPlannerSensorBase):
    """Sensor displaying the configured feed-in price threshold."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry, device_suffix: str = "") -> None:
        """Initialize the feed-in price threshold sensor."""
        super().__init__(coordinator, entry, device_suffix)
        self._attr_name = "Feed-in Price Threshold"
        self._attr_unique_id = f"{entry.entry_id}_feedin_price_threshold"
        self._attr_icon = "mdi:solar-power"
        self._attr_device_class = None
        self._attr_unit_of_measurement = "€/kWh"

    @property
    def native_value(self) -> float:
        """Return the configured feed-in price threshold."""
        return self.coordinator.config.get(CONF_FEEDIN_PRICE_THRESHOLD, DEFAULT_FEEDIN_PRICE_THRESHOLD)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        if not self.coordinator.data:
            return {}

        current_price = self.coordinator.data.get("price_analysis", {}).get("current_price")
        threshold = self.native_value

        return {
            "description": "Price above which solar export to grid is enabled",
            "current_price": current_price,
            "price_above_threshold": current_price >= threshold if current_price else None,
            "margin": round(current_price - threshold, 3) if current_price else None,
            "feedin_enabled": self.coordinator.data.get("feedin_solar", False),
        }


class FeedinPriceSensor(ElectricityPlannerSensorBase):
    """Sensor showing the effective feed-in price used for decisions."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry, device_suffix: str = "") -> None:
        super().__init__(coordinator, entry, device_suffix)
        self._attr_name = "Current Feed-in Price"
        self._attr_unique_id = f"{entry.entry_id}_feedin_price"
        self._attr_icon = "mdi:solar-power"
        self._attr_device_class = SensorDeviceClass.MONETARY
        self._attr_native_unit_of_measurement = "€/kWh"

    @property
    def native_value(self) -> float | None:
        """Return the effective feed-in price (after adjustments)."""
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get("feedin_effective_price")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic information for feed-in pricing."""
        if not self.coordinator.data:
            return {}

        price_analysis = self.coordinator.data.get("price_analysis", {})
        power_allocation = self.coordinator.data.get("power_allocation", {})

        return {
            "raw_market_price": price_analysis.get("raw_current_price"),
            "consumption_multiplier": price_analysis.get("price_adjustment_multiplier"),
            "consumption_offset": price_analysis.get("price_adjustment_offset"),
            "feedin_multiplier": self.coordinator.config.get(
                CONF_FEEDIN_ADJUSTMENT_MULTIPLIER, DEFAULT_FEEDIN_ADJUSTMENT_MULTIPLIER
            ),
            "feedin_offset": self.coordinator.config.get(
                CONF_FEEDIN_ADJUSTMENT_OFFSET, DEFAULT_FEEDIN_ADJUSTMENT_OFFSET
            ),
            "feedin_threshold": self.coordinator.config.get(
                CONF_FEEDIN_PRICE_THRESHOLD, DEFAULT_FEEDIN_PRICE_THRESHOLD
            ),
            "feedin_enabled": self.coordinator.data.get("feedin_solar", False),
            "feedin_reason": self.coordinator.data.get("feedin_solar_reason", ""),
            "remaining_solar": power_allocation.get("remaining_solar"),
        }


class BuyPriceMarginSensor(ElectricityPlannerSensorBase):
    """Sensor exposing the difference between current price and threshold."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry, device_suffix: str = "") -> None:
        super().__init__(coordinator, entry, device_suffix)
        self._attr_name = "Buy Price Margin"
        self._attr_unique_id = f"{entry.entry_id}_buy_price_margin"
        self._attr_icon = "mdi:currency-eur"
        self._attr_native_unit_of_measurement = None
        self._attr_device_class = None
        self._attr_state_class = None

    @property
    def native_value(self) -> str | None:
        price_analysis = self.coordinator.data.get("price_analysis", {})
        current_price = price_analysis.get("current_price")
        threshold = price_analysis.get("price_threshold")

        if current_price is None or threshold is None:
            return None
        margin = current_price - threshold
        if current_price <= threshold:
            return "favorable"
        if margin <= 0.02:
            return "watch"
        return "expensive"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        price_analysis = self.coordinator.data.get("price_analysis", {})
        threshold = price_analysis.get("price_threshold")
        current_price = price_analysis.get("current_price")
        margin = (current_price - threshold) if (current_price is not None and threshold is not None) else None
        return {
            "current_price": current_price,
            "price_threshold": threshold,
            "margin": round(margin, 4) if margin is not None else None,
            "very_low_price": price_analysis.get("very_low_price"),
        }


class FeedinPriceMarginSensor(ElectricityPlannerSensorBase):
    """Sensor exposing the difference between feed-in price and threshold."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry, device_suffix: str = "") -> None:
        super().__init__(coordinator, entry, device_suffix)
        self._attr_name = "Feed-in Price Margin"
        self._attr_unique_id = f"{entry.entry_id}_feedin_price_margin"
        self._attr_icon = "mdi:solar-power"
        self._attr_native_unit_of_measurement = None
        self._attr_device_class = None
        self._attr_state_class = None

    @property
    def native_value(self) -> str | None:
        threshold = self.coordinator.config.get(
            CONF_FEEDIN_PRICE_THRESHOLD, DEFAULT_FEEDIN_PRICE_THRESHOLD
        )
        effective_price = self.coordinator.data.get("feedin_effective_price")

        if effective_price is None:
            return None
        margin = threshold - effective_price
        if effective_price >= threshold:
            return "profitable"
        if margin <= 0.02:
            return "near"
        return "loss"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        threshold = self.coordinator.config.get(
            CONF_FEEDIN_PRICE_THRESHOLD, DEFAULT_FEEDIN_PRICE_THRESHOLD
        )
        effective_price = self.coordinator.data.get("feedin_effective_price")
        margin = (threshold - effective_price) if effective_price is not None else None
        return {
            "threshold": threshold,
            "effective_price": effective_price,
            "margin": round(margin, 4) if margin is not None else None,
        }


class VeryLowPriceThresholdSensor(ElectricityPlannerSensorBase):
    """Sensor displaying the very low price threshold percentage."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry, device_suffix: str = "") -> None:
        """Initialize the very low price threshold sensor."""
        super().__init__(coordinator, entry, device_suffix)
        self._attr_name = "Very Low Price Threshold"
        self._attr_unique_id = f"{entry.entry_id}_very_low_price_threshold"
        self._attr_icon = "mdi:percent"
        self._attr_device_class = None
        self._attr_unit_of_measurement = "%"

    @property
    def native_value(self) -> int:
        """Return the configured very low price threshold."""
        return self.coordinator.config.get(CONF_VERY_LOW_PRICE_THRESHOLD, DEFAULT_VERY_LOW_PRICE_THRESHOLD)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        if not self.coordinator.data:
            return {}

        price_analysis = self.coordinator.data.get("price_analysis", {})
        price_position = price_analysis.get("price_position")
        very_low_price = price_analysis.get("very_low_price", False)
        threshold = self.native_value / 100.0

        return {
            "description": "Bottom percentage of daily price range considered 'very low'",
            "price_position": f"{price_position:.0%}" if price_position is not None else None,
            "is_very_low_price": very_low_price,
            "threshold_decimal": threshold,
        }


class SignificantSolarThresholdSensor(ElectricityPlannerSensorBase):
    """Sensor displaying the significant solar threshold."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry, device_suffix: str = "") -> None:
        """Initialize the significant solar threshold sensor."""
        super().__init__(coordinator, entry, device_suffix)
        self._attr_name = "Significant Solar Threshold"
        self._attr_unique_id = f"{entry.entry_id}_significant_solar_threshold"
        self._attr_icon = "mdi:solar-power"
        self._attr_device_class = SensorDeviceClass.POWER
        self._attr_native_unit_of_measurement = "W"

    @property
    def native_value(self) -> int:
        """Return the configured significant solar threshold."""
        return self.coordinator.config.get(CONF_SIGNIFICANT_SOLAR_THRESHOLD, DEFAULT_SIGNIFICANT_SOLAR_THRESHOLD)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        if not self.coordinator.data:
            return {}

        power_analysis = self.coordinator.data.get("power_analysis", {})
        solar_surplus = power_analysis.get("solar_surplus", 0)
        has_significant_surplus = power_analysis.get("significant_solar_surplus", False)

        return {
            "description": "Solar surplus power level considered significant enough to prefer over grid",
            "current_solar_surplus": solar_surplus,
            "has_significant_surplus": has_significant_surplus,
            "margin": solar_surplus - self.native_value,
        }


class EmergencySOCThresholdSensor(ElectricityPlannerSensorBase):
    """Sensor displaying the emergency SOC threshold."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry, device_suffix: str = "") -> None:
        """Initialize the emergency SOC threshold sensor."""
        super().__init__(coordinator, entry, device_suffix)
        self._attr_name = "Emergency SOC Threshold"
        self._attr_unique_id = f"{entry.entry_id}_emergency_soc_threshold"
        self._attr_icon = "mdi:battery-alert"
        self._attr_device_class = None
        self._attr_unit_of_measurement = "%"

    @property
    def native_value(self) -> int:
        """Return the configured emergency SOC threshold."""
        return self.coordinator.config.get(CONF_EMERGENCY_SOC_THRESHOLD, DEFAULT_EMERGENCY_SOC)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        if not self.coordinator.data:
            return {}

        battery_analysis = self.coordinator.data.get("battery_analysis", {})
        average_soc = battery_analysis.get("average_soc")
        is_emergency = average_soc < self.native_value if average_soc is not None else None

        return {
            "description": "SOC level below which emergency charging is triggered regardless of price",
            "average_soc": average_soc,
            "is_emergency": is_emergency,
            "margin": average_soc - self.native_value if average_soc is not None else None,
        }


class NordPoolPricesSensor(ElectricityPlannerSensorBase):
    """Sensor exposing Nord Pool prices for today and tomorrow for dashboard visualization."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator, entry: ConfigEntry, device_suffix: str = "") -> None:
        """Initialize the Nord Pool prices sensor."""
        super().__init__(coordinator, entry, device_suffix)
        self._attr_name = "Nord Pool Prices"
        self._attr_unique_id = f"{entry.entry_id}_nordpool_prices"
        self._attr_icon = "mdi:chart-line"
        self._attr_device_class = None
        self._attr_native_unit_of_measurement = None

    @property
    def native_value(self) -> str | None:
        """Return the sensor state (summary of available data)."""
        prices_today = self.coordinator.data.get("nordpool_prices_today")
        prices_tomorrow = self.coordinator.data.get("nordpool_prices_tomorrow")

        if not prices_today and not prices_tomorrow:
            return "unavailable"

        # Count total entries across all areas
        today_count = sum(len(v) for v in prices_today.values() if isinstance(v, list)) if prices_today else 0
        tomorrow_count = sum(len(v) for v in prices_tomorrow.values() if isinstance(v, list)) if prices_tomorrow else 0

        if today_count > 0 and tomorrow_count > 0:
            return f"today+tomorrow ({today_count + tomorrow_count} intervals)"
        elif today_count > 0:
            return f"today only ({today_count} intervals)"
        elif tomorrow_count > 0:
            return f"tomorrow only ({tomorrow_count} intervals)"
        return "unavailable"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return price data as attributes for use in dashboard cards."""
        prices_today = self.coordinator.data.get("nordpool_prices_today")
        prices_tomorrow = self.coordinator.data.get("nordpool_prices_tomorrow")

        # Transport cost lookup/status provided by coordinator
        transport_lookup = self.coordinator.data.get("transport_cost_lookup") or []
        transport_status = self.coordinator.data.get("transport_cost_status", "not_configured")

        # Combine today and tomorrow prices into a single list for easier dashboard usage
        combined_prices: list[dict[str, Any]] = []

        if prices_today:
            # Get first area (or you could make this configurable)
            area_code = next(iter(prices_today.keys()), None)
            if area_code:
                for interval in prices_today[area_code]:
                    normalized = self._normalize_price_interval(interval, transport_lookup)
                    if normalized:
                        combined_prices.append(normalized)

        if prices_tomorrow:
            area_code = next(iter(prices_tomorrow.keys()), None)
            if area_code:
                for interval in prices_tomorrow[area_code]:
                    normalized = self._normalize_price_interval(interval, transport_lookup)
                    if normalized:
                        combined_prices.append(normalized)

        # Calculate some useful statistics
        price_values = [price_entry["price"] for price_entry in combined_prices]

        if price_values:
            min_price = min(price_values)
            max_price = max(price_values)
            avg_price = sum(price_values) / len(price_values)
        else:
            min_price = max_price = avg_price = None

        # Trim historical intervals to keep recorder-friendly payload size while
        # preserving the full forward-looking forecast used by dashboards/thresholds.
        now = dt_util.now()
        future_prices: list[dict[str, Any]] = []
        for price_entry in combined_prices:
            start_raw = price_entry.get("start")
            if not start_raw:
                continue
            start_dt = dt_util.parse_datetime(start_raw)
            if start_dt and start_dt >= now:
                future_prices.append(price_entry)
        limited_prices = future_prices if future_prices else combined_prices

        return {
            "data": limited_prices,  # Limited price data for ApexCharts (already in €/kWh)
            "today_available": prices_today is not None,
            "tomorrow_available": prices_tomorrow is not None,
            "total_intervals": len(combined_prices),
            "displayed_intervals": len(limited_prices),
            "min_price": round(min_price, 4) if min_price is not None else None,
            "max_price": round(max_price, 4) if max_price is not None else None,
            "avg_price": round(avg_price, 4) if avg_price is not None else None,
            "price_range": round(max_price - min_price, 4) if (max_price is not None and min_price is not None) else None,
            "transport_cost_applied": (
                True
                if transport_status in ("applied", "fallback_current")
                else False
                if transport_status in ("pending_history", "error")
                else None
            ),
            "transport_cost_status": transport_status,
            "last_update": dt_util.now().isoformat(),
        }

    @staticmethod
    def _extract_price_value(data: dict[str, Any]) -> float | None:
        """Return a numeric price from the Nord Pool entry dict."""
        for key in ("value", "value_exc_vat", "price"):
            value = data.get(key)
            if isinstance(value, (int, float)):
                return float(value)

            if isinstance(value, str):
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue

        return None

    def _normalize_price_interval(self, interval: Any, transport_cost_lookup: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
        """Return a normalized interval dict with a guaranteed price key.

        Converts price from €/MWh to €/kWh and applies contract adjustments
        (multiplier and offset) and transport cost for the complete buy price.
        """
        if not isinstance(interval, dict):
            return None

        price_value = self._extract_price_value(interval)
        if price_value is None:
            return None

        # Convert from €/MWh to €/kWh
        price_kwh = price_value / 1000

        # Apply the same multiplier and offset as the decision engine
        multiplier = self.coordinator.config.get(
            CONF_PRICE_ADJUSTMENT_MULTIPLIER, DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER
        )
        offset = self.coordinator.config.get(
            CONF_PRICE_ADJUSTMENT_OFFSET, DEFAULT_PRICE_ADJUSTMENT_OFFSET
        )

        adjusted_price = (price_kwh * multiplier) + offset

        # Add transport cost based on the interval's hour
        # Use lookup table built from historical data
        transport_cost = 0.0
        fallback_transport = self.coordinator.data.get("transport_cost")
        applied_lookup_cost = False

        if transport_cost_lookup:
            start_time_str = interval.get("start")
            interval_start = dt_util.parse_datetime(start_time_str) if start_time_str else None
            if interval_start is not None:
                interval_start_utc = dt_util.as_utc(interval_start)
            else:
                interval_start_utc = None

            if interval_start_utc is not None:
                now = dt_util.utcnow()
                effective_cost: float | None = None

                # For future times, look for the same hour from 1 week ago
                if interval_start_utc > now:
                    week_ago = interval_start_utc - timedelta(days=7)
                    # Find the cost that was active at this same time last week
                    cost_from_pattern: float | None = None
                    for change in transport_cost_lookup:
                        change_start_str = change.get("start")
                        change_cost = change.get("cost")
                        if change_cost is None:
                            continue
                        if change_start_str is None:
                            cost_from_pattern = float(change_cost)
                            continue
                        change_start = dt_util.parse_datetime(change_start_str)
                        if change_start is None:
                            continue
                        change_start_utc = dt_util.as_utc(change_start)
                        if change_start_utc <= week_ago:
                            cost_from_pattern = float(change_cost)
                        else:
                            break

                    if cost_from_pattern is not None:
                        effective_cost = cost_from_pattern

                # For past/current times or if no week-old data, use most recent cost
                if effective_cost is None:
                    for change in transport_cost_lookup:
                        change_start_str = change.get("start")
                        change_cost = change.get("cost")
                        if change_cost is None:
                            continue
                        if change_start_str is None:
                            effective_cost = float(change_cost)
                            continue
                        change_start = dt_util.parse_datetime(change_start_str)
                        if change_start is None:
                            continue
                        change_start_utc = dt_util.as_utc(change_start)
                        if change_start_utc <= interval_start_utc:
                            effective_cost = float(change_cost)
                        else:
                            break

                if effective_cost is not None:
                    transport_cost = effective_cost
                    applied_lookup_cost = True

        if not applied_lookup_cost and fallback_transport is not None:
            transport_cost = fallback_transport

        final_price = adjusted_price + transport_cost

        normalized = dict(interval)
        normalized["price"] = final_price
        normalized["transport_cost"] = transport_cost
        return normalized
