"""Sensor platform for Electricity Planner."""

from __future__ import annotations

import logging
from datetime import timedelta
from enum import Enum
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry

try:
    from homeassistant.const import EntityCategory
except ImportError:

    class EntityCategory(str, Enum):
        """Fallback entity-category enum for older Home Assistant test deps."""

        CONFIG = "config"
        DIAGNOSTIC = "diagnostic"


from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    CONF_BASE_GRID_SETPOINT,
    CONF_BUY_VAT_MULTIPLIER,
    CONF_EMERGENCY_SOC_THRESHOLD,
    CONF_FEEDIN_ADJUSTMENT_MULTIPLIER,
    CONF_FEEDIN_ADJUSTMENT_OFFSET,
    CONF_FEEDIN_PRICE_THRESHOLD,
    CONF_INVERTER_DERATING_SOC_BYPASS_THRESHOLD,
    CONF_INVERTER_DERATING_UNUSED_RELEASE_MINUTES,
    CONF_INVERTER_EXPORT_DEADBAND,
    CONF_INVERTER_EXPORT_LIMIT,
    CONF_MAX_INVERTER_POWER,
    CONF_PHASE_MODE,
    CONF_PRICE_ADJUSTMENT_MULTIPLIER,
    CONF_PRICE_ADJUSTMENT_OFFSET,
    CONF_PRICE_THRESHOLD,
    CONF_SIGNIFICANT_SOLAR_THRESHOLD,
    CONF_SUNNY_FORECAST_THRESHOLD_KWH,
    CONF_VERY_LOW_PRICE_THRESHOLD,
    DEFAULT_BASE_GRID_SETPOINT,
    DEFAULT_BUY_VAT_MULTIPLIER,
    DEFAULT_EMERGENCY_SOC,
    DEFAULT_FEEDIN_ADJUSTMENT_MULTIPLIER,
    DEFAULT_FEEDIN_ADJUSTMENT_OFFSET,
    DEFAULT_FEEDIN_PRICE_THRESHOLD,
    DEFAULT_INVERTER_DERATING_SOC_BYPASS_THRESHOLD,
    DEFAULT_INVERTER_DERATING_UNUSED_RELEASE_MINUTES,
    DEFAULT_INVERTER_EXPORT_DEADBAND,
    DEFAULT_INVERTER_EXPORT_LIMIT,
    DEFAULT_MAX_INVERTER_POWER,
    DEFAULT_MAX_SOC,
    DEFAULT_MAX_SOC_SOLAR,
    DEFAULT_MAX_SOC_SUNNY,
    DEFAULT_MONTHLY_PEAK_SAFETY_MARGIN,
    DEFAULT_PHASE_NAMES,
    DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER,
    DEFAULT_PRICE_ADJUSTMENT_OFFSET,
    DEFAULT_PRICE_THRESHOLD,
    DEFAULT_SIGNIFICANT_SOLAR_THRESHOLD,
    DEFAULT_SUNNY_FORECAST_THRESHOLD_KWH,
    DEFAULT_VERY_LOW_PRICE_THRESHOLD,
    DOMAIN,
    INTEGRATION_VERSION,
    PHASE_IDS,
    PHASE_MODE_SINGLE,
    PHASE_MODE_THREE,
)
from .coordinator import ElectricityPlannerCoordinator
from .helpers import (
    extract_price_from_interval,
    is_in_month_peak_transition_window,
    parse_datetime_cached,
)

_LOGGER = logging.getLogger(__name__)

# Home Assistant's recorder silently drops state attributes > 16 KB. A trimmed
# Nord Pool interval (start + 3 floats) is ~95 bytes, so 150 intervals plus
# ~200 bytes of summary headers stays well under the limit while still giving
# the dashboard chart 6h of history plus the forward-looking forecast.
_MAX_RECORDER_INTERVALS = 150


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    coordinator = hass.data[DOMAIN][entry.entry_id]

    # AUTOMATION SENSORS: Essential power sensors for automations
    automation_entities = [
        ChargerLimitSensor(coordinator, entry, "_automation"),
        GridSetpointSensor(coordinator, entry, "_automation"),
        InverterDeratingTargetSensor(coordinator, entry, "_automation"),
    ]
    if coordinator.config.get(CONF_PHASE_MODE, PHASE_MODE_SINGLE) == PHASE_MODE_THREE:
        automation_entities.extend(
            PhaseGridSetpointSensor(coordinator, entry, phase_id, "_automation")
            for phase_id in PHASE_IDS
        )

    # DIAGNOSTIC SENSORS: For monitoring and troubleshooting only
    diagnostic_entities = [
        ChargingDecisionSensor(coordinator, entry, "_diagnostic"),
        BatteryAnalysisSensor(coordinator, entry, "_diagnostic"),
        PriceAnalysisSensor(coordinator, entry, "_diagnostic"),
        PowerAnalysisSensor(coordinator, entry, "_diagnostic"),
        DataAvailabilitySensor(coordinator, entry, "_diagnostic"),
        EntityStatusSensor(coordinator, entry, "_diagnostic"),
        DecisionDiagnosticsSensor(coordinator, entry, "_diagnostic"),
        ForecastInsightsSensor(coordinator, entry, "_diagnostic"),
        PriceThresholdSensor(coordinator, entry, "_diagnostic"),
        FeedinPriceThresholdSensor(coordinator, entry, "_diagnostic"),
        BuyPriceMarginSensor(coordinator, entry, "_diagnostic"),
        FeedinPriceMarginSensor(coordinator, entry, "_diagnostic"),
        FeedinPriceSensor(coordinator, entry, "_diagnostic"),
        BuyVatMultiplierSensor(coordinator, entry, "_diagnostic"),
        VeryLowPriceThresholdSensor(coordinator, entry, "_diagnostic"),
        SignificantSolarThresholdSensor(coordinator, entry, "_diagnostic"),
        EmergencySOCThresholdSensor(coordinator, entry, "_diagnostic"),
        NordPoolPricesSensor(coordinator, entry, "_diagnostic"),
    ]

    entities = automation_entities + diagnostic_entities

    async_add_entities(entities, False)


class ElectricityPlannerSensorBase(CoordinatorEntity, SensorEntity):
    """Base class for Electricity Planner sensors."""

    def __init__(
        self,
        coordinator: ElectricityPlannerCoordinator,
        entry: ConfigEntry,
        device_suffix: str = "",
    ) -> None:
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

    def __init__(
        self,
        coordinator: ElectricityPlannerCoordinator,
        entry: ConfigEntry,
        device_suffix: str = "",
    ) -> None:
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
            battery_reason = self.coordinator.data.get(
                "battery_grid_charging_reason", ""
            )
            return f"charge_battery: {battery_reason}"
        elif car_grid:
            car_reason = self.coordinator.data.get("car_grid_charging_reason", "")
            return f"charge_car: {car_reason}"
        else:
            # Show the most relevant reason for not charging
            battery_reason = self.coordinator.data.get(
                "battery_grid_charging_reason", ""
            )
            return f"no_charging: {battery_reason}"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        if not self.coordinator.data:
            return {"data_available": False}

        price_data_available = self.coordinator.data.get("price_analysis", {}).get(
            "data_available", False
        )

        attributes = {
            "battery_grid_charging": self.coordinator.data.get("battery_grid_charging"),
            "car_grid_charging": self.coordinator.data.get("car_grid_charging"),
            "battery_grid_charging_reason": self.coordinator.data.get(
                "battery_grid_charging_reason"
            ),
            "car_grid_charging_reason": self.coordinator.data.get(
                "car_grid_charging_reason"
            ),
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

    def __init__(
        self,
        coordinator: ElectricityPlannerCoordinator,
        entry: ConfigEntry,
        device_suffix: str = "",
    ) -> None:
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
            "remaining_capacity_percent": battery_analysis.get(
                "remaining_capacity_percent"
            ),
        }


class PriceAnalysisSensor(ElectricityPlannerSensorBase):
    """Sensor for price analysis."""

    def __init__(
        self,
        coordinator: ElectricityPlannerCoordinator,
        entry: ConfigEntry,
        device_suffix: str = "",
    ) -> None:
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
            "price_adjustment_multiplier": price_analysis.get(
                "price_adjustment_multiplier"
            ),
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

    def __init__(
        self,
        coordinator: ElectricityPlannerCoordinator,
        entry: ConfigEntry,
        device_suffix: str = "",
    ) -> None:
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
            "house_consumption_without_car": power_analysis.get(
                "house_consumption_without_car"
            ),
            "solar_surplus": power_analysis.get(
                "solar_surplus"
            ),  # Available excess for batteries/car/export
            "car_charging_power": power_analysis.get("car_charging_power"),
            "has_solar_production": power_analysis.get("has_solar_production"),
            "has_solar_surplus": power_analysis.get(
                "has_solar_surplus"
            ),  # Has available excess solar
            "significant_solar_surplus": power_analysis.get(
                "significant_solar_surplus"
            ),
            "car_currently_charging": power_analysis.get("car_currently_charging"),
            "available_surplus_for_batteries": power_analysis.get(
                "available_surplus_for_batteries"
            ),
            "solar_coverage_ratio": power_analysis.get("solar_coverage_ratio"),
            "has_excess_solar_available": power_analysis.get(
                "has_excess_solar_available"
            ),  # Available for any use
        }

        # Add grid power snapshot (if configured)
        grid_power = self.coordinator.data.get("grid_power")
        if grid_power is not None:
            attributes["grid_power"] = grid_power
            attributes["grid_import"] = max(0, grid_power)  # Positive value for import
            attributes["grid_export"] = abs(
                min(0, grid_power)
            )  # Positive value for export

        # Expose peak import limiting status
        attributes["car_peak_limited"] = bool(
            self.coordinator.data.get("car_peak_limited", False)
        )
        peak_threshold = self.coordinator.data.get("car_peak_limit_threshold")
        if peak_threshold is not None:
            attributes["car_peak_limit_threshold_w"] = peak_threshold

        return attributes


class DataAvailabilitySensor(ElectricityPlannerSensorBase):
    """Sensor for data availability duration tracking."""

    def __init__(
        self,
        coordinator: ElectricityPlannerCoordinator,
        entry: ConfigEntry,
        device_suffix: str = "",
    ) -> None:
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

        unavailable_duration = (
            dt_util.utcnow() - self.coordinator.data_unavailable_since
        )
        return int(unavailable_duration.total_seconds())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        if not self.coordinator.data:
            return {}

        attributes = {}

        # Add availability information
        if self.coordinator.last_successful_update:
            attributes["last_successful_update"] = (
                self.coordinator.last_successful_update.isoformat()
            )

        if self.coordinator.data_unavailable_since:
            attributes["data_unavailable_since"] = (
                self.coordinator.data_unavailable_since.isoformat()
            )

        attributes.update(
            {
                "notification_sent": self.coordinator.notification_sent,
                "data_currently_available": self.coordinator.is_data_available(),
            }
        )

        return attributes


class EntityStatusSensor(ElectricityPlannerSensorBase):
    """Sensor showing availability status of all configured entities.

    Shows at a glance which entities are available/unavailable,
    helping users understand which data the planner is considering.
    """

    def __init__(
        self,
        coordinator: ElectricityPlannerCoordinator,
        entry: ConfigEntry,
        device_suffix: str = "",
    ) -> None:
        """Initialize the entity status sensor."""
        super().__init__(coordinator, entry, device_suffix)
        self._attr_name = "Entity Status"
        self._attr_unique_id = f"{entry.entry_id}_entity_status"
        self._attr_icon = "mdi:check-network"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> str | None:
        """Return summary status of configured entities."""
        if not self.coordinator.data:
            return "unknown"

        entity_status = self.coordinator.data.get("entity_status", {})
        summary = entity_status.get("summary", {})

        available = summary.get("available", 0)
        unavailable = summary.get("unavailable", 0)
        total = summary.get("total_configured", 0)

        if total == 0:
            return "no_entities"
        if unavailable == 0:
            return "all_available"
        if available == 0:
            return "all_unavailable"
        return f"{unavailable}_unavailable"

    @property
    def icon(self) -> str:
        """Return icon based on status."""
        if not self.coordinator.data:
            return "mdi:help-network-outline"

        entity_status = self.coordinator.data.get("entity_status", {})
        summary = entity_status.get("summary", {})
        unavailable = summary.get("unavailable", 0)
        required_unavailable = summary.get("required_unavailable", [])

        if required_unavailable:
            return "mdi:alert-circle"  # Red alert - required entities missing
        if unavailable > 0:
            return "mdi:alert"  # Warning - optional entities missing
        return "mdi:check-network"  # All good

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return detailed entity status as attributes."""
        if not self.coordinator.data:
            return {}

        entity_status = self.coordinator.data.get("entity_status", {})

        # Flatten the entity status for easy reading
        attributes = {
            "summary": entity_status.get("summary", {}),
        }

        # Add flat lists of unavailable entities for quick visibility
        unavailable_entities = []
        available_entities = []

        for category_name in [
            "price_entities",
            "battery_entities",
            "power_entities",
            "optional_entities",
        ]:
            category = entity_status.get(category_name, {})
            for name, status in category.items():
                if not status.get("configured"):
                    continue
                entity_id = status.get("entity_id", name)
                entity_state = status.get("status", "unknown")

                if entity_state == "available":
                    available_entities.append(entity_id)
                else:
                    unavailable_entities.append(
                        {
                            "entity_id": entity_id,
                            "status": entity_state,
                            "reason": status.get("reason", "Unknown"),
                            "is_required": status.get("is_required", False),
                        }
                    )

        attributes["available_entities"] = available_entities
        attributes["unavailable_entities"] = unavailable_entities

        # Add detailed breakdown by category for diagnostics
        attributes["price_entities"] = entity_status.get("price_entities", {})
        attributes["battery_entities"] = entity_status.get("battery_entities", {})
        attributes["power_entities"] = entity_status.get("power_entities", {})
        attributes["optional_entities"] = entity_status.get("optional_entities", {})

        return attributes


class ChargerLimitSensor(ElectricityPlannerSensorBase):
    """AUTOMATION SENSOR (4/5): Car charger power limit in Watts."""

    def __init__(
        self,
        coordinator: ElectricityPlannerCoordinator,
        entry: ConfigEntry,
        device_suffix: str = "",
    ) -> None:
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
            "charger_limit_reason": self.coordinator.data.get(
                "charger_limit_reason", ""
            ),
            "current_car_power": self.coordinator.data.get("power_analysis", {}).get(
                "car_charging_power", 0
            ),
            "solar_surplus": self.coordinator.data.get("power_analysis", {}).get(
                "solar_surplus", 0
            ),
            "car_currently_charging": self.coordinator.data.get(
                "power_analysis", {}
            ).get("car_currently_charging", False),
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

    def __init__(
        self,
        coordinator: ElectricityPlannerCoordinator,
        entry: ConfigEntry,
        device_suffix: str = "",
    ) -> None:
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
        base_grid_setpoint = self.coordinator.config.get(
            CONF_BASE_GRID_SETPOINT, DEFAULT_BASE_GRID_SETPOINT
        )
        try:
            monthly_peak_value = max(0, int(float(monthly_peak or 0)))
        except (TypeError, ValueError):
            monthly_peak_value = 0
        month_peak_transition_active = is_in_month_peak_transition_window(
            now=dt_util.utcnow()
        )
        applied_monthly_peak = 0 if month_peak_transition_active else monthly_peak_value
        controlling_peak = max(applied_monthly_peak, base_grid_setpoint)
        peak_based_grid_setpoint = int(
            controlling_peak * DEFAULT_MONTHLY_PEAK_SAFETY_MARGIN
        )
        max_grid_setpoint = peak_based_grid_setpoint

        attributes = {
            "grid_setpoint_reason": self.coordinator.data.get(
                "grid_setpoint_reason", ""
            ),
            "charger_limit": self.coordinator.data.get("charger_limit", 0),
            "current_car_power": self.coordinator.data.get("power_analysis", {}).get(
                "car_charging_power", 0
            ),
            "solar_surplus": self.coordinator.data.get("power_analysis", {}).get(
                "solar_surplus", 0
            ),
            "battery_average_soc": self.coordinator.data.get(
                "battery_analysis", {}
            ).get("average_soc", 0),
            "arbitrage_mode_active": self.coordinator.data.get(
                "arbitrage_mode_active", False
            ),
            "arbitrage_mode_export_power": self.coordinator.data.get(
                "arbitrage_mode_export_power", 0
            ),
            "arbitrage_mode_configured_export_cap_w": (
                self.coordinator.data.get("arbitrage_mode_plan", {}).get(
                    "configured_export_cap_w"
                )
            ),
            "monthly_grid_peak": monthly_peak,
            "applied_monthly_grid_peak": applied_monthly_peak,
            "month_peak_transition_active": month_peak_transition_active,
            "base_grid_setpoint": base_grid_setpoint,
            "controlling_peak": controlling_peak,
            "peak_based_grid_setpoint": peak_based_grid_setpoint,
            "max_grid_setpoint": max_grid_setpoint,
        }

        attributes["phase_mode"] = self.coordinator.data.get(
            "phase_mode", PHASE_MODE_SINGLE
        )

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


class PhaseGridSetpointSensor(ElectricityPlannerSensorBase):
    """AUTOMATION SENSOR: Per-phase grid power setpoint in Watts."""

    def __init__(
        self,
        coordinator: ElectricityPlannerCoordinator,
        entry: ConfigEntry,
        phase_id: str,
        device_suffix: str = "",
    ) -> None:
        """Initialize the phase grid setpoint sensor."""
        super().__init__(coordinator, entry, device_suffix)
        self._phase_id = phase_id
        self._phase_label = DEFAULT_PHASE_NAMES.get(phase_id, phase_id)
        self._attr_name = f"Grid Setpoint {self._phase_label}"
        self._attr_unique_id = f"{entry.entry_id}_grid_setpoint_{phase_id}"
        self._attr_icon = "mdi:transmission-tower"
        self._attr_native_unit_of_measurement = "W"
        self._attr_device_class = SensorDeviceClass.POWER

    @property
    def native_value(self) -> int:
        """Return the recommended grid setpoint for this phase."""
        if not self.coordinator.data:
            return 0

        phase_results = self.coordinator.data.get("phase_results") or {}
        phase_result = phase_results.get(self._phase_id) or {}
        return int(phase_result.get("grid_setpoint", 0) or 0)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return phase-specific setpoint attributes."""
        if not self.coordinator.data:
            return {}

        phase_results = self.coordinator.data.get("phase_results") or {}
        phase_result = phase_results.get(self._phase_id) or {}
        phase_details = self.coordinator.data.get("phase_details") or {}
        details = phase_details.get(self._phase_id) or {}
        grid_components = phase_result.get("grid_components") or {}

        return {
            "phase_id": self._phase_id,
            "phase_name": details.get("name", self._phase_label),
            "aggregate_grid_setpoint": self.coordinator.data.get("grid_setpoint", 0),
            "actual_grid_power": details.get("grid_power"),
            "battery_component": grid_components.get("battery", 0),
            "car_component": grid_components.get("car", 0),
            "charger_limit": phase_result.get("charger_limit", 0),
            "battery_grid_charging": phase_result.get("battery_grid_charging", False),
            "car_grid_charging": phase_result.get("car_grid_charging", False),
            "battery_grid_charging_reason": phase_result.get(
                "battery_grid_charging_reason"
            ),
            "car_grid_charging_reason": phase_result.get("car_grid_charging_reason"),
            "battery_entities": phase_result.get("battery_entities", []),
            "capacity_share": phase_result.get("capacity_share"),
            "capacity_share_kwh": phase_result.get("capacity_share_kwh"),
        }


class InverterDeratingTargetSensor(ElectricityPlannerSensorBase):
    """AUTOMATION SENSOR: Recommended inverter derating target in Watts."""

    def __init__(
        self,
        coordinator: ElectricityPlannerCoordinator,
        entry: ConfigEntry,
        device_suffix: str = "",
    ) -> None:
        """Initialize the inverter derating target sensor."""
        super().__init__(coordinator, entry, device_suffix)
        self._attr_name = "Inverter Derating Target"
        self._attr_unique_id = f"{entry.entry_id}_inverter_derating_target"
        self._attr_icon = "mdi:solar-power-variant-outline"
        self._attr_native_unit_of_measurement = "W"
        self._attr_device_class = SensorDeviceClass.POWER

    @property
    def native_value(self) -> int | None:
        """Return the recommended inverter derating target."""
        if not self.coordinator.data:
            return None

        return self.coordinator.data.get("inverter_derating_target")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        if not self.coordinator.data:
            return {}

        return {
            "inverter_derating_reason": self.coordinator.data.get(
                "inverter_derating_reason", ""
            ),
            "inverter_derating_alarm": self.coordinator.data.get(
                "inverter_derating_alarm", False
            ),
            "inverter_derating_alarm_reason": self.coordinator.data.get(
                "inverter_derating_alarm_reason", ""
            ),
            "feedin_allowed": self.coordinator.data.get("feedin_solar", False),
            "feedin_reason": self.coordinator.data.get("feedin_solar_reason", ""),
            "grid_power": self.coordinator.data.get("grid_power"),
            "solar_production": self.coordinator.data.get("solar_production"),
            "house_consumption": self.coordinator.data.get("house_consumption"),
            "export_limit_w": self.coordinator.config.get(
                CONF_INVERTER_EXPORT_LIMIT, DEFAULT_INVERTER_EXPORT_LIMIT
            ),
        }


class DecisionDiagnosticsSensor(ElectricityPlannerSensorBase):
    """Comprehensive diagnostics sensor exposing all decision parameters for validation."""

    def __init__(
        self,
        coordinator: ElectricityPlannerCoordinator,
        entry: ConfigEntry,
        device_suffix: str = "",
    ) -> None:
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
        self.coordinator.data.get("solar_analysis", {})
        time_context = self.coordinator.data.get("time_context", {})
        phase_results = self.coordinator.data.get("phase_results", {})
        phase_grid_setpoints = {
            phase: result.get("grid_setpoint", 0)
            for phase, result in phase_results.items()
        }

        # Configuration values (for validation)
        config = self.coordinator.config

        return {
            # Main decisions
            "decisions": {
                "battery_grid_charging": self.coordinator.data.get(
                    "battery_grid_charging", False
                ),
                "car_grid_charging": self.coordinator.data.get(
                    "car_grid_charging", False
                ),
                "feedin_solar": self.coordinator.data.get("feedin_solar", False),
                "arbitrage_mode_enabled": self.coordinator.data.get(
                    "arbitrage_mode_enabled", False
                ),
                "arbitrage_mode_active": self.coordinator.data.get(
                    "arbitrage_mode_active", False
                ),
                "battery_reason": self.coordinator.data.get(
                    "battery_grid_charging_reason", ""
                ),
                "car_reason": self.coordinator.data.get("car_grid_charging_reason", ""),
                "feedin_reason": self.coordinator.data.get("feedin_solar_reason", ""),
                "arbitrage_reason": self.coordinator.data.get(
                    "arbitrage_mode_reason", ""
                ),
                "phase_grid_setpoints": phase_grid_setpoints,
                "manual_overrides": self.coordinator.data.get("manual_overrides", {}),
            },
            # Strategy evaluation order
            "strategy_trace": self.coordinator.data.get("strategy_trace", []),
            "forecast_summary": self.coordinator.data.get("forecast_summary", {}),
            # Power outputs
            "power_outputs": {
                "charger_limit": self.coordinator.data.get("charger_limit", 0),
                "grid_setpoint": self.coordinator.data.get("grid_setpoint", 0),
                "inverter_derating_target": self.coordinator.data.get(
                    "inverter_derating_target", 0
                ),
                "charger_limit_reason": self.coordinator.data.get(
                    "charger_limit_reason", ""
                ),
                "grid_setpoint_reason": self.coordinator.data.get(
                    "grid_setpoint_reason", ""
                ),
                "inverter_derating_reason": self.coordinator.data.get(
                    "inverter_derating_reason", ""
                ),
                "inverter_derating_alarm": self.coordinator.data.get(
                    "inverter_derating_alarm", False
                ),
                "inverter_derating_alarm_reason": self.coordinator.data.get(
                    "inverter_derating_alarm_reason", ""
                ),
                "arbitrage_mode_plan": self.coordinator.data.get(
                    "arbitrage_mode_plan", {}
                ),
                "grid_components": self.coordinator.data.get("grid_components", {}),
                "phase_mode": self.coordinator.data.get(
                    "phase_mode", PHASE_MODE_SINGLE
                ),
                "phase_results": phase_results,
                "phase_grid_setpoints": phase_grid_setpoints,
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
                "price_adjustment_multiplier": price_analysis.get(
                    "price_adjustment_multiplier"
                ),
                "price_adjustment_offset": price_analysis.get(
                    "price_adjustment_offset"
                ),
                "price_threshold": price_analysis.get("price_threshold"),
                "average_threshold": self.coordinator.data.get("average_threshold"),
                "is_low_price": price_analysis.get("is_low_price", False),
                "very_low_price": price_analysis.get("very_low_price", False),
                "price_position": price_analysis.get("price_position"),
                "significant_price_drop": price_analysis.get(
                    "significant_price_drop", False
                ),
                "data_available": price_analysis.get("data_available", False),
            },
            # Battery analysis (for validation)
            "battery_analysis": {
                "average_soc": battery_analysis.get("average_soc"),
                "min_soc": battery_analysis.get("min_soc"),
                "max_soc": battery_analysis.get("max_soc"),
                "batteries_count": battery_analysis.get("batteries_count", 0),
                "batteries_full": battery_analysis.get("batteries_full", False),
                "batteries_available": battery_analysis.get(
                    "batteries_available", True
                ),
                "min_soc_threshold": battery_analysis.get("min_soc_threshold"),
                "max_soc_threshold": battery_analysis.get("max_soc_threshold"),
            },
            # Power analysis (for validation)
            "power_analysis": {
                "solar_surplus": power_analysis.get("solar_surplus"),
                "car_charging_power": power_analysis.get("car_charging_power"),
                "has_solar_surplus": power_analysis.get("has_solar_surplus", False),
                "significant_solar_surplus": power_analysis.get(
                    "significant_solar_surplus", False
                ),
                "car_currently_charging": power_analysis.get(
                    "car_currently_charging", False
                ),
            },
            # Power allocation (critical for validation)
            "power_allocation": {
                "solar_for_batteries": power_allocation.get("solar_for_batteries", 0),
                "solar_for_car": power_allocation.get("solar_for_car", 0),
                "car_current_solar_usage": power_allocation.get(
                    "car_current_solar_usage", 0
                ),
                "remaining_solar": power_allocation.get("remaining_solar", 0),
                "total_allocated": power_allocation.get("total_allocated", 0),
                "allocation_reason": power_allocation.get("allocation_reason", ""),
            },
            # Time context (for validation)
            "time_context": {
                "current_hour": time_context.get("current_hour"),
            },
            # Configuration values (for validation)
            "configured_limits": {
                "emergency_soc": config.get("emergency_soc_threshold", 15),
                "max_battery_power": config.get("max_battery_power", 3000),
                "max_car_power": config.get("max_car_power", 11000),
                "max_grid_power": config.get("max_grid_power", 15000),
                "max_inverter_power": config.get(
                    CONF_MAX_INVERTER_POWER, DEFAULT_MAX_INVERTER_POWER
                ),
                "min_car_charging_threshold": config.get(
                    "min_car_charging_threshold", 100
                ),
                "min_car_charging_duration": config.get("min_car_charging_duration", 2),
                "predictive_charging_min_soc": config.get(
                    "predictive_charging_min_soc", 30
                ),
                "significant_solar_threshold": config.get(
                    "significant_solar_threshold", 1000
                ),
                "very_low_price_threshold": config.get("very_low_price_threshold", 30),
                "price_threshold": config.get("price_threshold", 0.15),
                "feedin_price_threshold": config.get("feedin_price_threshold", 0.05),
                "inverter_export_limit": config.get(
                    CONF_INVERTER_EXPORT_LIMIT, DEFAULT_INVERTER_EXPORT_LIMIT
                ),
                "inverter_export_deadband": config.get(
                    CONF_INVERTER_EXPORT_DEADBAND, DEFAULT_INVERTER_EXPORT_DEADBAND
                ),
                "inverter_derating_unused_release_minutes": config.get(
                    CONF_INVERTER_DERATING_UNUSED_RELEASE_MINUTES,
                    DEFAULT_INVERTER_DERATING_UNUSED_RELEASE_MINUTES,
                ),
                "inverter_derating_soc_bypass_threshold": config.get(
                    CONF_INVERTER_DERATING_SOC_BYPASS_THRESHOLD,
                    DEFAULT_INVERTER_DERATING_SOC_BYPASS_THRESHOLD,
                ),
                "max_soc_threshold": config.get("max_soc_threshold", DEFAULT_MAX_SOC),
                "max_soc_threshold_sunny": config.get(
                    "max_soc_threshold_sunny", DEFAULT_MAX_SOC_SUNNY
                ),
                "max_soc_threshold_solar": config.get(
                    "max_soc_threshold_solar", DEFAULT_MAX_SOC_SOLAR
                ),
                "sunny_forecast_threshold_kwh": config.get(
                    CONF_SUNNY_FORECAST_THRESHOLD_KWH,
                    DEFAULT_SUNNY_FORECAST_THRESHOLD_KWH,
                ),
            },
            # Solar forecast / sunny day status
            "solar_forecast": {
                "sunny_day_active": self.coordinator.data.get(
                    "sunny_day_active", False
                ),
                "solar_forecast_kwh": self.coordinator.data.get(
                    "solar_forecast_production"
                ),
                "solar_forecast_source": self.coordinator.data.get(
                    "solar_forecast_source"
                ),
                "solar_forecast_entity": config.get("solar_forecast_entity"),
                "solar_forecast_today_entity": config.get(
                    "solar_forecast_today_entity"
                ),
            },
            # Validation flags (for quick problem identification)
            "validation_flags": {
                "price_data_valid": price_analysis.get("data_available", False),
                "battery_data_valid": battery_analysis.get("batteries_available", True),
                "power_allocation_valid": power_allocation.get("total_allocated", 0)
                <= power_analysis.get("solar_surplus", 0),
                "emergency_override_active": self._check_emergency_override(
                    battery_analysis, config
                ),
                "predictive_logic_active": self._check_predictive_logic(
                    price_analysis, battery_analysis, config
                ),
            },
            # Last update
            "last_evaluation": self.coordinator.data.get("next_evaluation", "unknown"),
        }

    def _check_emergency_override(self, battery_analysis: dict, config: dict) -> bool:
        """Check if any emergency overrides are currently active."""
        average_soc = battery_analysis.get("average_soc")
        if average_soc is None:
            return False

        emergency_soc = config.get("emergency_soc_threshold", 15)
        return average_soc < emergency_soc

    def _check_predictive_logic(
        self, price_analysis: dict, battery_analysis: dict, config: dict
    ) -> bool:
        """Check if predictive charging logic is active."""
        significant_price_drop = price_analysis.get("significant_price_drop", False)
        is_low_price = price_analysis.get("is_low_price", False)
        average_soc = battery_analysis.get("average_soc")
        predictive_min_soc = config.get("predictive_charging_min_soc", 30)

        if average_soc is None:
            return False

        return (
            is_low_price and significant_price_drop and average_soc > predictive_min_soc
        )


class ForecastInsightsSensor(ElectricityPlannerSensorBase):
    """Sensor exposing upcoming price forecast insights."""

    def __init__(
        self,
        coordinator: ElectricityPlannerCoordinator,
        entry: ConfigEntry,
        device_suffix: str = "",
    ) -> None:
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

    def __init__(
        self,
        coordinator: ElectricityPlannerCoordinator,
        entry: ConfigEntry,
        device_suffix: str = "",
    ) -> None:
        """Initialize the price threshold sensor."""
        super().__init__(coordinator, entry, device_suffix)
        self._attr_name = "Price Threshold"
        self._attr_unique_id = f"{entry.entry_id}_price_threshold"
        self._attr_icon = "mdi:currency-eur"
        self._attr_device_class = None
        self._attr_native_unit_of_measurement = "€/kWh"

    @property
    def native_value(self) -> float:
        """Return the configured price threshold."""
        return self.coordinator.config.get(
            CONF_PRICE_THRESHOLD, DEFAULT_PRICE_THRESHOLD
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        if not self.coordinator.data:
            return {}

        current_price = self.coordinator.data.get("price_analysis", {}).get(
            "current_price"
        )
        threshold = self.native_value

        return {
            "description": "Price below which grid charging is considered favorable",
            "current_price": current_price,
            "price_below_threshold": (
                current_price < threshold if current_price is not None else None
            ),
            "margin": (
                round(current_price - threshold, 3)
                if current_price is not None
                else None
            ),
        }


class FeedinPriceThresholdSensor(ElectricityPlannerSensorBase):
    """Sensor displaying the configured feed-in price threshold."""

    def __init__(
        self,
        coordinator: ElectricityPlannerCoordinator,
        entry: ConfigEntry,
        device_suffix: str = "",
    ) -> None:
        """Initialize the feed-in price threshold sensor."""
        super().__init__(coordinator, entry, device_suffix)
        self._attr_name = "Feed-in Price Threshold"
        self._attr_unique_id = f"{entry.entry_id}_feedin_price_threshold"
        self._attr_icon = "mdi:solar-power"
        self._attr_device_class = None
        self._attr_native_unit_of_measurement = "€/kWh"

    @property
    def native_value(self) -> float:
        """Return the configured feed-in price threshold."""
        return self.coordinator.config.get(
            CONF_FEEDIN_PRICE_THRESHOLD, DEFAULT_FEEDIN_PRICE_THRESHOLD
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        if not self.coordinator.data:
            return {}

        effective_price = self.coordinator.data.get("feedin_effective_price")
        threshold = self.native_value

        return {
            "description": "Price above which solar export to grid is enabled",
            "current_price": effective_price,
            "effective_price": effective_price,
            "price_above_threshold": (
                effective_price >= threshold if effective_price is not None else None
            ),
            "margin": (
                round(effective_price - threshold, 3)
                if effective_price is not None
                else None
            ),
            "feedin_enabled": self.coordinator.data.get("feedin_solar", False),
        }


class FeedinPriceSensor(ElectricityPlannerSensorBase):
    """Sensor showing the effective feed-in price used for decisions."""

    def __init__(
        self,
        coordinator: ElectricityPlannerCoordinator,
        entry: ConfigEntry,
        device_suffix: str = "",
    ) -> None:
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


class BuyVatMultiplierSensor(ElectricityPlannerSensorBase):
    """Sensor exposing the configured buy-side VAT multiplier."""

    def __init__(
        self,
        coordinator: ElectricityPlannerCoordinator,
        entry: ConfigEntry,
        device_suffix: str = "",
    ) -> None:
        super().__init__(coordinator, entry, device_suffix)
        self._attr_name = "Buy VAT Multiplier"
        self._attr_unique_id = f"{entry.entry_id}_buy_vat_multiplier"
        self._attr_icon = "mdi:percent"
        self._attr_native_unit_of_measurement = None
        self._attr_device_class = None
        self._attr_state_class = None
        self._attr_suggested_display_precision = 2

    @property
    def native_value(self) -> float:
        """Return the configured buy-side VAT multiplier."""
        return self.coordinator.config.get(
            CONF_BUY_VAT_MULTIPLIER, DEFAULT_BUY_VAT_MULTIPLIER
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return VAT-related diagnostic information."""
        multiplier = self.native_value
        return {
            "description": (
                "VAT multiplier applied to the buy/import price only "
                "(feed-in/sell prices are not multiplied by VAT)"
            ),
            "vat_percentage": round((multiplier - 1.0) * 100, 1),
        }


class BuyPriceMarginSensor(ElectricityPlannerSensorBase):
    """Sensor exposing the difference between current price and threshold."""

    def __init__(
        self,
        coordinator: ElectricityPlannerCoordinator,
        entry: ConfigEntry,
        device_suffix: str = "",
    ) -> None:
        super().__init__(coordinator, entry, device_suffix)
        self._attr_name = "Buy Price Margin"
        self._attr_unique_id = f"{entry.entry_id}_buy_price_margin"
        self._attr_icon = "mdi:currency-eur"
        self._attr_native_unit_of_measurement = None
        self._attr_device_class = None
        self._attr_state_class = None

    @property
    def native_value(self) -> str | None:
        if not self.coordinator.data:
            return None
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
        if not self.coordinator.data:
            return {}
        price_analysis = self.coordinator.data.get("price_analysis", {})
        threshold = price_analysis.get("price_threshold")
        current_price = price_analysis.get("current_price")
        margin = (
            (current_price - threshold)
            if (current_price is not None and threshold is not None)
            else None
        )
        return {
            "current_price": current_price,
            "price_threshold": threshold,
            "margin": round(margin, 4) if margin is not None else None,
            "very_low_price": price_analysis.get("very_low_price"),
        }


class FeedinPriceMarginSensor(ElectricityPlannerSensorBase):
    """Sensor exposing the difference between feed-in price and threshold."""

    def __init__(
        self,
        coordinator: ElectricityPlannerCoordinator,
        entry: ConfigEntry,
        device_suffix: str = "",
    ) -> None:
        super().__init__(coordinator, entry, device_suffix)
        self._attr_name = "Feed-in Price Margin"
        self._attr_unique_id = f"{entry.entry_id}_feedin_price_margin"
        self._attr_icon = "mdi:solar-power"
        self._attr_native_unit_of_measurement = None
        self._attr_device_class = None
        self._attr_state_class = None

    @property
    def native_value(self) -> str | None:
        if not self.coordinator.data:
            return None
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
        if not self.coordinator.data:
            return {}
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

    def __init__(
        self,
        coordinator: ElectricityPlannerCoordinator,
        entry: ConfigEntry,
        device_suffix: str = "",
    ) -> None:
        """Initialize the very low price threshold sensor."""
        super().__init__(coordinator, entry, device_suffix)
        self._attr_name = "Very Low Price Threshold"
        self._attr_unique_id = f"{entry.entry_id}_very_low_price_threshold"
        self._attr_icon = "mdi:percent"
        self._attr_device_class = None
        self._attr_native_unit_of_measurement = "%"

    @property
    def native_value(self) -> int:
        """Return the configured very low price threshold."""
        return self.coordinator.config.get(
            CONF_VERY_LOW_PRICE_THRESHOLD, DEFAULT_VERY_LOW_PRICE_THRESHOLD
        )

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
            "price_position": (
                f"{price_position:.0%}" if price_position is not None else None
            ),
            "is_very_low_price": very_low_price,
            "threshold_decimal": threshold,
        }


class SignificantSolarThresholdSensor(ElectricityPlannerSensorBase):
    """Sensor displaying the significant solar threshold."""

    def __init__(
        self,
        coordinator: ElectricityPlannerCoordinator,
        entry: ConfigEntry,
        device_suffix: str = "",
    ) -> None:
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
        return self.coordinator.config.get(
            CONF_SIGNIFICANT_SOLAR_THRESHOLD, DEFAULT_SIGNIFICANT_SOLAR_THRESHOLD
        )

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

    def __init__(
        self,
        coordinator: ElectricityPlannerCoordinator,
        entry: ConfigEntry,
        device_suffix: str = "",
    ) -> None:
        """Initialize the emergency SOC threshold sensor."""
        super().__init__(coordinator, entry, device_suffix)
        self._attr_name = "Emergency SOC Threshold"
        self._attr_unique_id = f"{entry.entry_id}_emergency_soc_threshold"
        self._attr_icon = "mdi:battery-alert"
        self._attr_device_class = None
        self._attr_native_unit_of_measurement = "%"

    @property
    def native_value(self) -> int:
        """Return the configured emergency SOC threshold."""
        return self.coordinator.config.get(
            CONF_EMERGENCY_SOC_THRESHOLD, DEFAULT_EMERGENCY_SOC
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        if not self.coordinator.data:
            return {}

        battery_analysis = self.coordinator.data.get("battery_analysis", {})
        average_soc = battery_analysis.get("average_soc")
        is_emergency = (
            average_soc < self.native_value if average_soc is not None else None
        )

        return {
            "description": "SOC level below which emergency charging is triggered regardless of price",
            "average_soc": average_soc,
            "is_emergency": is_emergency,
            "margin": (
                average_soc - self.native_value if average_soc is not None else None
            ),
        }


class NordPoolPricesSensor(ElectricityPlannerSensorBase):
    """Sensor exposing Nord Pool prices for today and tomorrow for dashboard visualization."""

    def __init__(
        self,
        coordinator: ElectricityPlannerCoordinator,
        entry: ConfigEntry,
        device_suffix: str = "",
    ) -> None:
        """Initialize the Nord Pool prices sensor."""
        super().__init__(coordinator, entry, device_suffix)
        self._attr_name = "Nord Pool Prices"
        self._attr_unique_id = f"{entry.entry_id}_nordpool_prices"
        self._attr_icon = "mdi:chart-line"
        self._attr_device_class = None
        self._attr_native_unit_of_measurement = None
        self._cached_attribute_signature: tuple[Any, ...] | None = None
        self._cached_attributes: dict[str, Any] | None = None
        self._cached_attributes_last_update: str | None = None

    @property
    def native_value(self) -> str | None:
        """Return the sensor state (summary of available data)."""
        if not self.coordinator.data:
            return "unavailable"
        prices_today = self.coordinator.data.get("nordpool_prices_today")
        prices_tomorrow = self.coordinator.data.get("nordpool_prices_tomorrow")

        if not prices_today and not prices_tomorrow:
            return "unavailable"

        # Count total entries across all areas
        today_count = (
            sum(len(v) for v in prices_today.values() if isinstance(v, list))
            if prices_today
            else 0
        )
        tomorrow_count = (
            sum(len(v) for v in prices_tomorrow.values() if isinstance(v, list))
            if prices_tomorrow
            else 0
        )

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
        if not self.coordinator.data:
            return {}
        prices_today = self.coordinator.data.get("nordpool_prices_today")
        prices_tomorrow = self.coordinator.data.get("nordpool_prices_tomorrow")

        # Transport cost lookup/status provided by coordinator
        transport_lookup = self.coordinator.data.get("transport_cost_lookup") or []
        transport_status = self.coordinator.data.get(
            "transport_cost_status", "not_configured"
        )
        now = dt_util.now()

        signature = self._attribute_cache_signature(
            prices_today,
            prices_tomorrow,
            transport_lookup,
            transport_status,
            now,
        )
        if (
            signature == self._cached_attribute_signature
            and self._cached_attributes is not None
        ):
            return dict(self._cached_attributes)

        # Combine today and tomorrow prices into a single list for easier dashboard usage
        combined_prices: list[dict[str, Any]] = []

        if prices_today:
            # Get first area (or you could make this configurable)
            area_code = next(iter(prices_today.keys()), None)
            if area_code:
                for interval in prices_today[area_code]:
                    normalized = self._normalize_price_interval(
                        interval, transport_lookup
                    )
                    if normalized:
                        compact = self._compact_price_interval(normalized)
                        if compact:
                            combined_prices.append(compact)

        if prices_tomorrow:
            area_code = next(iter(prices_tomorrow.keys()), None)
            if area_code:
                for interval in prices_tomorrow[area_code]:
                    normalized = self._normalize_price_interval(
                        interval, transport_lookup
                    )
                    if normalized:
                        compact = self._compact_price_interval(normalized)
                        if compact:
                            combined_prices.append(compact)

        # Calculate some useful statistics
        price_values = [price_entry["price"] for price_entry in combined_prices]

        if price_values:
            min_price = min(price_values)
            max_price = max(price_values)
            avg_price = sum(price_values) / len(price_values)
        else:
            min_price = max_price = avg_price = None

        # Trim historical intervals to keep recorder-friendly payload size while
        # preserving the forward-looking forecast used by dashboards/thresholds.
        # HA's recorder silently drops state attributes larger than 16 KB, so
        # we cap both time window (~6h history) and interval count to fit that
        # budget even for 15-minute Nord Pool data (~180 intervals across two
        # days otherwise). Dashboard chart only reads the ``data`` array, so
        # the cap preferentially retains the most recent + forward intervals.
        history_cutoff = now - timedelta(hours=6)
        recent_prices: list[dict[str, Any]] = []
        for price_entry in combined_prices:
            start_raw = price_entry.get("start")
            if not start_raw:
                continue
            start_dt = parse_datetime_cached(start_raw)
            if start_dt and start_dt >= history_cutoff:
                recent_prices.append(price_entry)
        limited_prices = recent_prices if recent_prices else combined_prices
        # Hard ceiling on total intervals (~14 KB worst case with four
        # fields per interval). Keeps the tail closest to NOW: for forward
        # forecasts that means dropping the oldest history entries first.
        if len(limited_prices) > _MAX_RECORDER_INTERVALS:
            limited_prices = limited_prices[-_MAX_RECORDER_INTERVALS:]

        self._cached_attributes_last_update = now.isoformat()
        attributes = {
            "data": limited_prices,  # Limited price data for ApexCharts (already in €/kWh)
            "today_available": prices_today is not None,
            "tomorrow_available": prices_tomorrow is not None,
            "total_intervals": len(combined_prices),
            "displayed_intervals": len(limited_prices),
            "min_price": round(min_price, 4) if min_price is not None else None,
            "max_price": round(max_price, 4) if max_price is not None else None,
            "avg_price": round(avg_price, 4) if avg_price is not None else None,
            "price_range": (
                round(max_price - min_price, 4)
                if (max_price is not None and min_price is not None)
                else None
            ),
            "transport_cost_applied": (
                True
                if transport_status in ("applied", "fallback_current", "builtin")
                else False if transport_status in ("pending_history", "error") else None
            ),
            "transport_cost_status": transport_status,
            "last_update": self._cached_attributes_last_update,
        }
        self._cached_attribute_signature = signature
        self._cached_attributes = attributes
        return dict(attributes)

    def _attribute_cache_signature(
        self,
        prices_today: dict[str, Any] | None,
        prices_tomorrow: dict[str, Any] | None,
        transport_lookup: list[dict[str, Any]],
        transport_status: str,
        now,
    ) -> tuple[Any, ...]:
        """Return a cheap identity-based key for the rendered attribute payload.

        ``DataUpdateCoordinator`` replaces ``coordinator.data`` wholesale on
        every refresh, so its ``id()`` plus the ids of the major nested
        containers is enough to detect a refresh without serialising any
        contents. Mutable scalars on the data dict that the test suite may
        flip in-place (transport status / current cost) are included as
        plain values so they still trigger a recompute.
        ``transport_lookup`` here can be a freshly-allocated empty list (the
        ``or []`` fallback in the caller), so we read the raw stored value to
        keep identity stable across calls when no lookup is configured.
        """
        coordinator_data = self.coordinator.data
        stored_lookup = coordinator_data.get("transport_cost_lookup")
        return (
            id(coordinator_data),
            id(prices_today),
            id(prices_tomorrow),
            id(stored_lookup),
            transport_status,
            coordinator_data.get("transport_cost"),
        )

    def _compact_price_interval(
        self, interval: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Return a recorder-friendly interval payload for dashboard rendering.

        Only fields consumed by the managed dashboard chart are kept so the
        attribute payload stays under Home Assistant's 16 KB recorder limit
        even with 48h of 15-minute intervals. Dropped compared to the
        internal normalized form: ``end``, ``adjusted_energy_price``,
        ``contract_adjustment`` (none referenced by the dashboard template).
        """
        start = interval.get("start")
        price = interval.get("price")
        if start is None or price is None:
            return None

        transport_cost = interval.get("transport_cost", 0.0)
        raw_price = interval.get("raw_price", 0.0)
        return {
            "start": start,
            "price": round(float(price), 6),
            "raw_price": round(float(raw_price), 6),
            "transport_cost": round(float(transport_cost), 6),
        }

    def _normalize_price_interval(
        self, interval: Any, transport_cost_lookup: list[dict[str, Any]] | None = None
    ) -> dict[str, Any] | None:
        """Return a normalized interval dict with a guaranteed price key.

        Converts price from €/MWh to €/kWh and applies contract adjustments
        (multiplier and offset) and transport cost for the complete buy price.
        """
        if not isinstance(interval, dict):
            return None

        price_value = extract_price_from_interval(interval)
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
        contract_adjustment = adjusted_price - price_kwh

        transport_cost = 0.0
        start_time_str = interval.get("start")
        interval_start = (
            parse_datetime_cached(start_time_str) if start_time_str else None
        )
        interval_start_utc = (
            dt_util.as_utc(interval_start) if interval_start is not None else None
        )

        # Use coordinator's unified transport cost resolution (handles both built-in and legacy)
        if interval_start_utc is not None:
            resolved = self.coordinator._resolve_transport_cost(
                transport_cost_lookup,
                interval_start_utc,
                reference_now=dt_util.utcnow(),
            )
            if resolved is not None:
                transport_cost = resolved
            else:
                fallback_transport = (
                    self.coordinator.data.get("transport_cost")
                    if self.coordinator.data
                    else None
                )
                if fallback_transport is not None:
                    transport_cost = fallback_transport
        else:
            fallback_transport = (
                self.coordinator.data.get("transport_cost")
                if self.coordinator.data
                else None
            )
            if fallback_transport is not None:
                transport_cost = fallback_transport

        final_price = adjusted_price + transport_cost

        normalized = dict(interval)
        normalized["price"] = final_price
        normalized["transport_cost"] = transport_cost
        normalized["raw_price"] = price_kwh
        normalized["adjusted_energy_price"] = adjusted_price
        normalized["contract_adjustment"] = contract_adjustment
        return normalized
