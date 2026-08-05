"""Charger limit calculation.

Extracted from ``decision_engine.py``. Advises the EV charger power cap
each cycle based on available solar, arbitrage discharge, grid
allowance, and peak-import protection.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

from .const import DEFAULT_MAX_SOC
from .defaults import DEFAULT_SYSTEM_LIMITS
from .grid_signs import sensor_grid_to_planner_power

if TYPE_CHECKING:
    from .decision_engine import CycleContext, EngineSettings
    from .grid_setpoint import GridSetpointCalculator

_LOGGER = logging.getLogger(__name__)

_GRID_TRACKING_DEADBAND_W = 500
_GRID_TRACKING_RELEASE_STEP_W = 250
_GRID_TRACKING_SETTLE_WINDOW = 180
_GRID_TRACKING_APPLICATION_TOLERANCE_W = 250


class ChargerLimitCalculator:
    """Compute advertised EV charger power limit for the cycle."""

    def __init__(
        self,
        settings: "EngineSettings",
        grid_setpoint: "GridSetpointCalculator",
    ) -> None:
        self._settings = settings
        self._grid_setpoint = grid_setpoint

    def refresh(self, settings: "EngineSettings") -> None:
        self._settings = settings

    @staticmethod
    def format_power_sources(sources: list[tuple[float, str]]) -> str:
        parts = [f"{int(amount)}W {label}" for amount, label in sources if amount > 0]
        if not parts:
            parts.append("0W available")
        return " + ".join(parts)

    def get_car_arbitrage_power(self, ctx: "CycleContext") -> int:
        return ctx.car_arbitrage_power

    def get_arbitrage_reserve_soc(self, ctx: "CycleContext") -> float:
        return ctx.arbitrage_mode_reserve_soc

    def apply_peak_import_limit(
        self,
        result: dict[str, Any],
        ctx: "CycleContext",
        *,
        non_grid_floor: float = 0.0,
    ) -> dict[str, Any]:
        if not ctx.car_peak_limited:
            return result

        limit = result.get("charger_limit", 0)
        if limit <= 0:
            return result

        preserved = max(0, min(int(non_grid_floor), limit))
        grid_portion = limit - preserved
        reduced_limit = preserved + grid_portion // 2

        if reduced_limit >= limit:
            return result

        existing_reason = result.get("charger_limit_reason", "")
        peak_reason = f"Peak import exceeded - reduced to {reduced_limit}W for 15min"

        result["charger_limit"] = reduced_limit
        result["charger_limit_reason"] = (
            f"{existing_reason} | {peak_reason}" if existing_reason else peak_reason
        )

        _LOGGER.info(
            "Peak import protection: reducing charger limit from %dW to %dW (preserved non-grid %dW)",
            limit,
            reduced_limit,
            preserved,
        )
        return result

    def apply_negative_import_feedback(
        self, result: dict[str, Any], ctx: "CycleContext"
    ) -> dict[str, Any]:
        """Reduce the EV cap when a negative-is-import P1 exceeds its target."""
        if not self._settings.grid_import_negative_feedback:
            result["grid_tracking_charger_limit"] = None
            result["grid_tracking_last_adjustment"] = None
            return result

        limit = int(result.get("charger_limit", 0))
        base_limit = limit
        target = int(result.get("grid_setpoint", 0))
        previous_cap = ctx.grid_tracking_charger_limit
        if not ctx.car_grid_charging or limit <= 0:
            result["grid_tracking_charger_limit"] = None
            result["grid_tracking_last_adjustment"] = None
            return result

        # Hold an existing correction during temporary EV power-sensor gaps.
        if previous_cap is not None:
            limit = min(limit, previous_cap)
            result["charger_limit"] = limit
        if (
            ctx.grid_tracking_average_power is None
            or ctx.car_charging_power <= self._settings.min_car_charging_threshold
        ):
            result["grid_tracking_charger_limit"] = previous_cap
            result["grid_tracking_last_adjustment"] = ctx.grid_tracking_last_adjustment
            result["grid_tracking_waiting_for_application"] = (
                ctx.grid_tracking_waiting_for_application
            )
            return result

        # Charger limits are applied asynchronously by Home Assistant.  Hold
        # this cap until measured EV power confirms it took effect.
        if ctx.grid_tracking_waiting_for_application and previous_cap is not None:
            if (
                ctx.car_charging_power
                > previous_cap + _GRID_TRACKING_APPLICATION_TOLERANCE_W
            ):
                result["grid_tracking_charger_limit"] = previous_cap
                result["grid_tracking_last_adjustment"] = (
                    ctx.grid_tracking_last_adjustment
                )
                result["grid_tracking_waiting_for_application"] = True
                return result
            result["grid_tracking_charger_limit"] = previous_cap
            result["grid_tracking_last_adjustment"] = ctx.grid_tracking_last_adjustment
            result["grid_tracking_limit_acknowledged"] = True
            result["grid_tracking_waiting_for_application"] = False
            return result

        now = dt_util.utcnow()
        if (
            ctx.grid_tracking_last_adjustment is not None
            and (now - ctx.grid_tracking_last_adjustment).total_seconds()
            < _GRID_TRACKING_SETTLE_WINDOW
        ):
            result["grid_tracking_charger_limit"] = previous_cap
            result["grid_tracking_last_adjustment"] = ctx.grid_tracking_last_adjustment
            return result

        # Convert the averaged raw P1 reading to the Planner target convention
        # before comparing it with the target.  The sign boundary is defined
        # once in grid_signs.py.
        average_grid_power = ctx.grid_tracking_average_power
        excess_import = max(
            0,
            int(
                sensor_grid_to_planner_power(average_grid_power)
                - target
                - _GRID_TRACKING_DEADBAND_W
            ),
        )
        if excess_import:
            # The 500W deadband is the permitted residual import, not an
            # action cap. Remove the measured excess, then wait for the full
            # settle window before taking another measurement.
            tracked_cap = max(0, limit - excess_import)
            result["charger_limit"] = tracked_cap
            result["charger_limit_reason"] = (
                f"{result.get('charger_limit_reason', '')} | Live P1 import "
                f"average {int(abs(average_grid_power))}W exceeds "
                f"{target}W Planner target by {excess_import}W; "
                f"limiting EV to {tracked_cap}W"
            )
        elif previous_cap is not None:
            # The full settle window already passed, so this is a recovered
            # average; release only one small step.
            tracked_cap = min(base_limit, previous_cap + _GRID_TRACKING_RELEASE_STEP_W)
            result["charger_limit"] = tracked_cap
            result["charger_limit_reason"] = (
                f"{result.get('charger_limit_reason', '')} | Live grid target recovered; "
                f"raising EV limit gradually to {tracked_cap}W"
            )
        else:
            tracked_cap = limit

        # Keep a just-released base cap long enough to verify that the
        # physical charger accepted the increase; clear it after that ack.
        result["grid_tracking_charger_limit"] = (
            tracked_cap
            if tracked_cap < base_limit
            or (previous_cap is not None and tracked_cap != previous_cap)
            else None
        )
        result["grid_tracking_last_adjustment"] = now
        result["grid_tracking_waiting_for_application"] = tracked_cap != previous_cap
        return result

    def calculate(
        self,
        battery_analysis: dict[str, Any],
        ctx: "CycleContext",
    ) -> dict[str, Any]:
        car_charging_power = ctx.car_charging_power
        min_threshold = self._settings.min_car_charging_threshold
        car_limit_cap = min(
            self._settings.max_car_power, DEFAULT_SYSTEM_LIMITS.max_car_charger_power
        )
        car_charging_allowed = ctx.car_grid_charging
        car_grid_import_allowed = ctx.car_grid_import_allowed
        car_solar_only = ctx.car_solar_only
        car_arbitrage_power = (
            self.get_car_arbitrage_power(ctx)
            if car_charging_allowed and not car_solar_only
            else 0
        )
        allocated_solar = ctx.allocated_car_solar
        solar_headroom = allocated_solar + ctx.remaining_solar

        if car_charging_power <= min_threshold and not (
            car_charging_allowed or car_solar_only or car_arbitrage_power > 0
        ):
            return {
                "charger_limit": 0,
                "charger_limit_reason": "Car not currently charging",
            }

        if car_solar_only:
            if allocated_solar > 0:
                limit = min(allocated_solar, car_limit_cap)
                return self.apply_peak_import_limit(
                    {
                        "charger_limit": int(limit),
                        "charger_limit_reason": f"Solar-only car charging - limited to allocated solar power ({int(limit)}W), no grid usage",
                    },
                    ctx,
                    non_grid_floor=allocated_solar,
                )
            return {
                "charger_limit": 0,
                "charger_limit_reason": "Solar-only mode but no solar available",
            }

        average_soc = battery_analysis.get("average_soc")
        max_soc_threshold = battery_analysis.get("max_soc_threshold", DEFAULT_MAX_SOC)
        arbitrage_reserve_soc = self.get_arbitrage_reserve_soc(ctx)

        if not car_grid_import_allowed and car_arbitrage_power <= 0:
            if (
                ctx.arbitrage_mode_active
                and average_soc is not None
                and average_soc < arbitrage_reserve_soc
            ):
                return {
                    "charger_limit": 0,
                    "charger_limit_reason": (
                        f"Battery {average_soc:.0f}% < arbitrage reserve {arbitrage_reserve_soc:.0f}% - "
                        "keeping arbitrage energy in the battery"
                    ),
                }
            return {
                "charger_limit": 0,
                "charger_limit_reason": "Car grid charging not allowed",
            }

        if average_soc is None:
            monthly_peak = ctx.monthly_grid_peak
            grid_allowance = (
                self._grid_setpoint.get_safe_setpoint(monthly_peak)
                if car_grid_import_allowed
                else 0
            )
            limit = min(solar_headroom + grid_allowance, car_limit_cap)
            sources = self.format_power_sources(
                [
                    (solar_headroom, "allocated solar"),
                    (grid_allowance, "grid"),
                ]
            )
            return self.apply_peak_import_limit(
                {
                    "charger_limit": int(limit),
                    "charger_limit_reason": (
                        "Battery data unavailable - conservative limit using "
                        f"{sources} ({int(limit)}W total)"
                    ),
                },
                ctx,
                non_grid_floor=solar_headroom,
            )

        monthly_peak = ctx.monthly_grid_peak
        max_setpoint = self._grid_setpoint.get_safe_setpoint(monthly_peak)
        grid_allowance = max_setpoint if car_grid_import_allowed else 0

        battery_grid_charging = ctx.battery_grid_charging
        predictive_min_soc = self._settings.predictive_min_soc

        if (
            average_soc < predictive_min_soc
            and battery_grid_charging
            and car_grid_import_allowed
        ):
            shared_grid_allowance = grid_allowance / 2
            limit = min(solar_headroom + shared_grid_allowance, car_limit_cap)
            _LOGGER.info(
                "Low SOC power sharing: battery %.0f%% < %.0f%%, limiting car to 50%% of grid (%dW)",
                average_soc,
                predictive_min_soc,
                int(limit),
            )
            sources = self.format_power_sources(
                [
                    (solar_headroom, "allocated solar"),
                    (shared_grid_allowance, "shared grid"),
                ]
            )
            return self.apply_peak_import_limit(
                {
                    "charger_limit": int(limit),
                    "charger_limit_reason": (
                        f"Low battery SOC ({average_soc:.0f}% < {predictive_min_soc}%) - "
                        f"sharing grid power with batteries using {sources} "
                        f"({int(limit)}W total)"
                    ),
                },
                ctx,
                non_grid_floor=solar_headroom,
            )

        if average_soc < max_soc_threshold and car_arbitrage_power <= 0:
            limit = min(solar_headroom + grid_allowance, car_limit_cap)
            sources = self.format_power_sources(
                [
                    (solar_headroom, "allocated solar"),
                    (grid_allowance, "grid"),
                ]
            )
            return self.apply_peak_import_limit(
                {
                    "charger_limit": int(limit),
                    "charger_limit_reason": (
                        f"Battery {average_soc:.0f}% < {max_soc_threshold}% - "
                        f"car can use {sources} ({int(limit)}W total), "
                        "surplus for batteries"
                    ),
                },
                ctx,
                non_grid_floor=solar_headroom,
            )

        available_power = solar_headroom + car_arbitrage_power + grid_allowance
        limit = min(available_power, car_limit_cap)
        sources = self.format_power_sources(
            [
                (solar_headroom, "allocated solar"),
                (car_arbitrage_power, "battery arbitrage"),
                (grid_allowance, "grid"),
            ]
        )

        threshold_context = (
            f"Battery {average_soc:.0f}% ≥ arbitrage reserve {arbitrage_reserve_soc:.0f}%"
            if car_arbitrage_power > 0 and average_soc < max_soc_threshold
            else f"Battery {average_soc:.0f}% ≥ {max_soc_threshold}%"
        )

        return self.apply_peak_import_limit(
            {
                "charger_limit": int(limit),
                "charger_limit_reason": (
                    f"{threshold_context} - "
                    f"car can use {sources} ({int(limit)}W total)"
                ),
            },
            ctx,
            non_grid_floor=solar_headroom + car_arbitrage_power,
        )
