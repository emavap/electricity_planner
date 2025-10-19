"""Calculate power limits and grid setpoints."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from .const import (
    DEFAULT_MONTHLY_PEAK_SAFETY_MARGIN,
    MAX_CAR_CHARGER_POWER,
)

_LOGGER = logging.getLogger(__name__)

def _get_safe_grid_setpoint(
    base_setpoint: int, monthly_peak: Optional[float]
) -> int:
    """Calculate safe grid setpoint based on monthly peak."""
    if monthly_peak and monthly_peak > base_setpoint:
        return int(monthly_peak * DEFAULT_MONTHLY_PEAK_SAFETY_MARGIN)
    return base_setpoint


def calculate_charger_limit(
    price_analysis: Dict[str, Any],
    battery_analysis: Dict[str, Any],
    power_allocation: Dict[str, Any],
    data: Dict[str, Any],
    settings: Any,
) -> Dict[str, Any]:
    """Calculate optimal charger power limit."""
    car_charging_power = data.get("car_charging_power", 0)
    min_threshold = settings.min_car_charging_threshold
    car_limit_cap = min(settings.max_car_power, MAX_CAR_CHARGER_POWER)
    car_grid_charging = data.get("car_grid_charging", False)
    car_solar_only = data.get("car_solar_only", False)
    allocated_solar = power_allocation.get("solar_for_car", 0)

    if car_charging_power <= min_threshold and not (car_grid_charging or car_solar_only):
        return {
            "charger_limit": 0,
            "charger_limit_reason": "Car not currently charging",
        }

    # Handle solar-only charging first
    if car_solar_only:
        if allocated_solar > 0:
            limit = min(allocated_solar, car_limit_cap)
            return {
                "charger_limit": int(limit),
                "charger_limit_reason": f"Solar-only car charging - limited to allocated solar power ({int(limit)}W), no grid usage",
            }
        else:
            return {
                "charger_limit": 0,
                "charger_limit_reason": "Solar-only mode but no solar available",
            }

    # If grid charging not allowed, set limit to 0
    if not car_grid_charging:
        return {
            "charger_limit": 0,
            "charger_limit_reason": "Car grid charging not allowed",
        }

    # Calculate based on battery SOC and grid limits
    # At this point: car_grid_charging=True and not solar_only
    average_soc = battery_analysis.get("average_soc")

    if average_soc is None:
        # No battery data available - use conservative grid-based limit
        monthly_peak = data.get("monthly_grid_peak", 0)
        max_setpoint = _get_safe_grid_setpoint(settings.base_grid_setpoint, monthly_peak)
        limit = min(max_setpoint, car_limit_cap)
        return {
            "charger_limit": int(limit),
            "charger_limit_reason": f"Battery data unavailable - conservative limit ({int(limit)}W)",
        }

    max_soc_threshold = battery_analysis.get("max_soc_threshold", 80)
    monthly_peak = data.get("monthly_grid_peak", 0)
    max_setpoint = _get_safe_grid_setpoint(settings.base_grid_setpoint, monthly_peak)

    if average_soc < max_soc_threshold:
        limit = min(max_setpoint, car_limit_cap)
        return {
            "charger_limit": int(limit),
            "charger_limit_reason": (f"Battery {average_soc:.0f}% < {max_soc_threshold}% - "
                                    f"car limited to grid setpoint ({int(limit)}W), surplus for batteries"),
        }

    available_surplus = power_allocation.get("remaining_solar", 0)
    available_power = available_surplus + max_setpoint
    limit = min(available_power, car_limit_cap)

    return {
        "charger_limit": int(limit),
        "charger_limit_reason": (f"Battery {average_soc:.0f}% ≥ {max_soc_threshold}% - "
                                f"car can use remaining surplus + grid ({int(limit)}W = "
                                f"{available_surplus}W + {max_setpoint}W)"),
    }


def calculate_grid_setpoint(
    price_analysis: Dict[str, Any],
    battery_analysis: Dict[str, Any],
    power_allocation: Dict[str, Any],
    data: Dict[str, Any],
    charger_limit: int,
    settings: Any,
) -> Dict[str, Any]:
    """Calculate grid setpoint based on energy management scenario."""
    car_charging_power = data.get("car_charging_power", 0)
    battery_grid_charging = data.get("battery_grid_charging", False)
    car_grid_charging = data.get("car_grid_charging", False)
    average_soc = battery_analysis.get("average_soc")

    min_threshold = settings.min_car_charging_threshold
    significant_car_charging = car_charging_power >= min_threshold

    # Handle unavailable battery data
    if average_soc is None:
        if significant_car_charging and car_grid_charging:
            monthly_peak = data.get("monthly_grid_peak", 0)
            max_setpoint = _get_safe_grid_setpoint(settings.base_grid_setpoint, monthly_peak)
            grid_setpoint = min(car_charging_power, max_setpoint)
            return {
                "grid_setpoint": int(grid_setpoint),
                "grid_setpoint_reason": f"Battery data unavailable - grid only for car ({int(grid_setpoint)}W)",
                "grid_components": {"battery": 0, "car": int(grid_setpoint)},
            }
        return {
            "grid_setpoint": 0,
            "grid_setpoint_reason": "Battery data unavailable - no grid power allocated",
            "grid_components": {"battery": 0, "car": 0},
        }

    # Solar-only car charging
    car_solar_only = data.get("car_solar_only", False)
    if significant_car_charging and car_solar_only:
        return {
            "grid_setpoint": 0,
            "grid_setpoint_reason": "Solar-only car charging detected - grid setpoint 0W",
            "grid_components": {"battery": 0, "car": 0},
        }

    # Calculate grid needs
    monthly_peak = data.get("monthly_grid_peak", 0)
    max_setpoint = _get_safe_grid_setpoint(settings.base_grid_setpoint, monthly_peak)

    grid_setpoint_parts = []
    car_grid_need = 0

    if significant_car_charging and car_grid_charging:
        allocated_solar = power_allocation.get("solar_for_car", 0)
        car_current_solar = power_allocation.get("car_current_solar_usage", 0)
        car_available_solar = allocated_solar + car_current_solar
        car_grid_need = max(0, min(car_charging_power - car_available_solar, max_setpoint))
        if car_grid_need > 0:
            grid_setpoint_parts.append(f"car {int(car_grid_need)}W")

    battery_grid_need = 0
    if battery_grid_charging:
        remaining_capacity = max(0, max_setpoint - car_grid_need)
        max_battery_power = settings.max_battery_power
        battery_grid_need = min(remaining_capacity, max_battery_power)
        if battery_grid_need > 0:
            grid_setpoint_parts.append(f"battery {int(battery_grid_need)}W")

    grid_setpoint = car_grid_need + battery_grid_need
    max_grid_power = settings.max_grid_power
    grid_setpoint = min(grid_setpoint, max_setpoint, max_grid_power)

    # Create reason
    if not grid_setpoint_parts:
        reason = "No grid charging needed"
    else:
        reason = f"Grid setpoint for {' + '.join(grid_setpoint_parts)} = {int(grid_setpoint)}W"
        if car_grid_need == 0 and significant_car_charging:
            reason += " (car charging not allowed)"

    return {
        "grid_setpoint": int(grid_setpoint),
        "grid_setpoint_reason": reason,
        "grid_components": {
            "battery": int(battery_grid_need),
            "car": int(car_grid_need),
        },
    }
