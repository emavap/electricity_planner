"""Solar-allocation calculation.

Extracted from ``decision_engine.py``. Splits the post-house solar
surplus between batteries, the EV and any leftover export, using a
car-state-aware policy that mirrors the behaviour documented in
``CLAUDE.md`` ("Solar Allocation Policy"). The engine retains thin
delegators so existing tests and monkeypatches keep working.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .defaults import DEFAULT_ALGORITHM_THRESHOLDS, DEFAULT_POWER_ESTIMATES
from .helpers import DataValidator, format_reason

if TYPE_CHECKING:
    from .decision_engine import EngineSettings

_LOGGER = logging.getLogger(__name__)


class SolarAllocationCalculator:
    """Allocate post-house solar surplus between batteries and the EV."""

    def __init__(
        self, settings: "EngineSettings", validator: DataValidator
    ) -> None:
        self._settings = settings
        self._validator = validator

    def refresh(self, settings: "EngineSettings") -> None:
        self._settings = settings

    def allocate(
        self,
        power_analysis: dict[str, Any],
        battery_analysis: dict[str, Any],
    ) -> dict[str, Any]:
        solar_surplus = power_analysis.get("solar_surplus", 0)
        significant_solar_threshold = self._settings.significant_solar_threshold
        car_charging_power = power_analysis.get("car_charging_power", 0) or 0
        min_car_threshold = self._settings.min_car_charging_threshold
        car_is_charging = car_charging_power > min_car_threshold

        solar_surplus = self._validator.validate_power_value(
            solar_surplus, name="solar_surplus"
        )

        battery_reserve_pool = (
            min(solar_surplus, significant_solar_threshold)
            if car_is_charging
            else solar_surplus
        )
        solar_for_batteries = self.battery_allocation(
            battery_reserve_pool, battery_analysis
        )

        available_for_car = max(0, solar_surplus - solar_for_batteries)
        if car_is_charging:
            car_current_solar_usage = self.car_solar_usage(
                power_analysis, available_for_car
            )
            additional_car_headroom = max(
                0,
                self._settings.max_car_power - car_current_solar_usage,
            )
            solar_for_car = min(
                max(0, available_for_car - car_current_solar_usage),
                additional_car_headroom,
            )
        else:
            car_current_solar_usage = 0
            solar_for_car = self.bootstrap_car_allocation(
                available_for_car, battery_analysis
            )

        total_allocated = solar_for_batteries + solar_for_car + car_current_solar_usage
        if total_allocated > solar_surplus:
            scale_factor = solar_surplus / total_allocated if total_allocated > 0 else 0
            solar_for_batteries = int(solar_for_batteries * scale_factor)
            solar_for_car = int(solar_for_car * scale_factor)
            car_current_solar_usage = int(car_current_solar_usage * scale_factor)
            _LOGGER.warning(
                "Power allocation %dW exceeds available solar %dW - scaled down",
                total_allocated, solar_surplus
            )

        remaining_solar = max(
            0,
            solar_surplus
            - solar_for_batteries
            - solar_for_car
            - car_current_solar_usage,
        )

        return {
            "solar_for_batteries": solar_for_batteries,
            "solar_for_car": solar_for_car,
            "car_current_solar_usage": car_current_solar_usage,
            "remaining_solar": remaining_solar,
            "total_allocated": solar_for_batteries + solar_for_car + car_current_solar_usage,
            "allocation_reason": format_reason(
                "Power allocation",
                f"Car using {car_current_solar_usage}W",
                {
                    "batteries": f"{solar_for_batteries}W",
                    "car_additional": f"{solar_for_car}W",
                    "remaining": f"{remaining_solar}W",
                    "total": f"{solar_surplus}W",
                },
            ),
        }

    def car_solar_usage(
        self, power_analysis: dict[str, Any], solar_surplus: float
    ) -> int:
        car_charging_power = power_analysis.get("car_charging_power", 0)
        min_threshold = self._settings.min_car_charging_threshold

        if car_charging_power > min_threshold and solar_surplus > 0:
            return min(car_charging_power, solar_surplus)
        return 0

    def battery_allocation(
        self, available_solar: float, battery_analysis: dict[str, Any]
    ) -> int:
        average_soc = battery_analysis.get("average_soc")
        solar_max = self._settings.max_soc_threshold_solar

        if average_soc is None or solar_max is None:
            return 0

        solar_full = average_soc >= solar_max

        if (
            not solar_full
            and average_soc < solar_max - DEFAULT_ALGORITHM_THRESHOLDS.soc_safety_margin
            and available_solar > 0
        ):
            soc_deficit = max(0, solar_max - average_soc)
            estimated_need = min(
                available_solar,
                int(soc_deficit * DEFAULT_POWER_ESTIMATES.per_soc_percent),
                self._settings.max_battery_power,
            )
            return max(0, estimated_need)
        return 0

    def bootstrap_car_allocation(
        self, available_solar: float, battery_analysis: dict[str, Any]
    ) -> int:
        if available_solar <= 0:
            return 0

        if battery_analysis.get("batteries_full"):
            return int(min(available_solar, self._settings.max_car_power))

        min_soc = battery_analysis.get("min_soc")
        if min_soc is None:
            return 0

        solar_max = self._settings.max_soc_threshold_solar
        solar_ready_threshold = solar_max - DEFAULT_ALGORITHM_THRESHOLDS.soc_buffer
        if min_soc >= solar_ready_threshold:
            return int(min(available_solar, self._settings.max_car_power))
        return 0
