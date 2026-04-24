"""Grid setpoint calculation.

Extracted from ``decision_engine.py``. Computes the grid import/export
setpoint each cycle, honouring the monthly-peak cap, transition window,
and safety nets that prevent authorised-only imports/exports.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from homeassistant.util import dt as dt_util

from .const import (
    DEFAULT_MONTHLY_PEAK_SAFETY_MARGIN,
    MONTH_PEAK_TRANSITION_LEAD_MINUTES,
)
from .helpers import is_in_month_peak_transition_window

if TYPE_CHECKING:
    from .decision_engine import CycleContext, EngineSettings

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class GridSetpointContext:
    """Resolved context for grid setpoint limits."""

    monthly_peak: int
    applied_monthly_peak: int
    base_setpoint: int
    controlling_peak: int
    effective_max_setpoint: int
    uses_monthly_peak: bool
    month_peak_transition_active: bool


def _safe_optional_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


class GridSetpointCalculator:
    """Calculate grid setpoint and its monthly-peak context."""

    def __init__(
        self,
        settings: "EngineSettings",
        car_arbitrage_power_provider: Callable[["CycleContext"], int],
    ) -> None:
        self._settings = settings
        self._car_arbitrage_power_provider = car_arbitrage_power_provider

    def refresh(self, settings: "EngineSettings") -> None:
        self._settings = settings

    def get_max_grid_power(self) -> int:
        return self._settings.max_grid_power

    def get_context(self, monthly_peak: float | None) -> GridSetpointContext:
        base_setpoint = self._settings.base_grid_setpoint
        monthly_peak_value = max(0, int(_safe_optional_float(monthly_peak) or 0))
        month_peak_transition_active = is_in_month_peak_transition_window(
            now=dt_util.utcnow()
        )
        applied_monthly_peak = 0 if month_peak_transition_active else monthly_peak_value
        uses_monthly_peak = applied_monthly_peak > base_setpoint
        controlling_peak = max(applied_monthly_peak, base_setpoint)
        effective_max_setpoint = int(
            controlling_peak * DEFAULT_MONTHLY_PEAK_SAFETY_MARGIN
        )
        return GridSetpointContext(
            monthly_peak=monthly_peak_value,
            applied_monthly_peak=applied_monthly_peak,
            base_setpoint=base_setpoint,
            controlling_peak=controlling_peak,
            effective_max_setpoint=effective_max_setpoint,
            uses_monthly_peak=uses_monthly_peak,
            month_peak_transition_active=month_peak_transition_active,
        )

    def get_safe_setpoint(self, monthly_peak: float | None) -> int:
        return self.get_context(monthly_peak).effective_max_setpoint

    def format_context_reason(self, context: GridSetpointContext) -> str:
        if (
            context.month_peak_transition_active
            and context.monthly_peak > context.base_setpoint
        ):
            return (
                f"Month changes in under {MONTH_PEAK_TRANSITION_LEAD_MINUTES}min, "
                "using next month baseline "
                f"{context.base_setpoint}W instead of current month peak "
                f"{context.monthly_peak}W, using {context.effective_max_setpoint}W "
                f"(90% of {context.controlling_peak}W)"
            )

        if context.monthly_peak <= 0:
            return (
                f"No current month peak available, max allowed peak is "
                f"{context.base_setpoint}W, using {context.effective_max_setpoint}W "
                f"(90% of {context.controlling_peak}W)"
            )

        return (
            f"Peak this month is {context.monthly_peak}W, max allowed peak is "
            f"{context.base_setpoint}W, using {context.effective_max_setpoint}W "
            f"(90% of {context.controlling_peak}W)"
        )

    def format_reason_for_peak(self, monthly_peak: float | None) -> str:
        return self.format_context_reason(self.get_context(monthly_peak))

    def calculate(
        self,
        price_analysis: dict[str, Any],
        battery_analysis: dict[str, Any],
        power_allocation: dict[str, Any],
        charger_limit: int,
        ctx: "CycleContext",
    ) -> dict[str, Any]:
        car_charging_power = ctx.car_charging_power
        battery_grid_charging = ctx.battery_grid_charging
        car_charging_allowed = ctx.car_grid_charging
        car_grid_import_allowed = ctx.car_grid_import_allowed
        car_solar_only = ctx.car_solar_only
        average_soc = battery_analysis.get("average_soc")
        monthly_peak = ctx.monthly_grid_peak
        peak_context = self.get_context(monthly_peak)
        peak_context_reason = self.format_context_reason(peak_context)
        car_arbitrage_power = (
            self._car_arbitrage_power_provider(ctx)
            if car_charging_allowed and not car_solar_only
            else 0
        )

        min_threshold = self._settings.min_car_charging_threshold
        planned_car_session = car_charging_allowed and charger_limit > 0
        significant_car_charging = car_charging_power > min_threshold
        active_or_planned_car_charging = significant_car_charging or planned_car_session
        battery_dump_active = ctx.battery_dump_active
        car_draws_from_grid = significant_car_charging and car_grid_import_allowed
        requested_car_power = car_charging_power
        solar_only_note = (
            "Solar-only car charging detected - car grid import blocked"
            if active_or_planned_car_charging and car_solar_only
            else None
        )

        if average_soc is None:
            if car_draws_from_grid:
                max_setpoint = peak_context.effective_max_setpoint
                effective_car_power = (
                    min(requested_car_power, charger_limit)
                    if charger_limit > 0
                    else requested_car_power
                )
                grid_setpoint = min(effective_car_power, max_setpoint)
                return {
                    "grid_setpoint": int(grid_setpoint),
                    "grid_setpoint_reason": (
                        f"Battery data unavailable - grid import reserved for car pulling {int(grid_setpoint)}W"
                        f" | {peak_context_reason}"
                    ),
                    "grid_components": {"battery": 0, "car": int(grid_setpoint)},
                }
            return {
                "grid_setpoint": 0,
                "grid_setpoint_reason": (
                    f"Battery data unavailable - no grid power allocated | {peak_context_reason}"
                ),
                "grid_components": {"battery": 0, "car": 0},
            }

        max_setpoint = peak_context.effective_max_setpoint

        grid_setpoint_parts: list[str] = []
        car_grid_need = 0
        car_battery_need = 0
        battery_dump_export_power = ctx.battery_dump_export_power

        if significant_car_charging and car_charging_allowed:
            car_available_solar = ctx.allocated_car_solar
            effective_car_power = (
                min(requested_car_power, charger_limit)
                if charger_limit > 0
                else requested_car_power
            )
            residual_car_need = max(0.0, effective_car_power - car_available_solar)
            car_battery_need = min(residual_car_need, car_arbitrage_power)
            residual_car_need = max(0.0, residual_car_need - car_battery_need)
            if car_solar_only:
                car_battery_need = 0
            if car_grid_import_allowed and not car_solar_only:
                car_grid_need = max(0, min(residual_car_need, max_setpoint))
            if car_grid_need > 0:
                grid_setpoint_parts.append(f"car pulling {int(car_grid_need)}W")
            if car_battery_need > 0:
                grid_setpoint_parts.append(
                    f"battery supplying car {int(car_battery_need)}W"
                )

        battery_grid_need = 0
        if battery_dump_active and battery_dump_export_power > 0:
            remaining_export_power = max(0, battery_dump_export_power - int(car_battery_need))
            if remaining_export_power > 0:
                battery_grid_need = -min(
                    remaining_export_power,
                    self._settings.max_battery_power,
                    self.get_max_grid_power(),
                )
                grid_setpoint_parts.append(
                    f"battery exporting {int(abs(battery_grid_need))}W"
                )
        elif battery_grid_charging:
            remaining_capacity = max(0, max_setpoint - car_grid_need)
            max_battery_power = self._settings.max_battery_power
            battery_grid_need = min(remaining_capacity, max_battery_power)
            if battery_grid_need > 0:
                grid_setpoint_parts.append(f"battery charging {int(battery_grid_need)}W")

        grid_setpoint = car_grid_need + battery_grid_need
        if grid_setpoint > 0:
            max_grid_power = self.get_max_grid_power()
            grid_setpoint = min(grid_setpoint, max_setpoint, max_grid_power)

        import_permitted = car_draws_from_grid or battery_grid_charging
        export_permitted = battery_dump_active
        gate_reason: str | None = None
        if grid_setpoint > 0 and not import_permitted:
            gate_reason = (
                "Grid import blocked - neither car nor battery charging authorised"
            )
            _LOGGER.info(
                "Grid setpoint safety net clamped %dW import to 0 "
                "(car_grid_need=%d, battery_grid_need=%d, "
                "significant_car_charging=%s, car_grid_import_allowed=%s, "
                "battery_grid_charging=%s)",
                int(grid_setpoint), int(car_grid_need), int(battery_grid_need),
                significant_car_charging, car_grid_import_allowed,
                battery_grid_charging,
            )
            grid_setpoint = 0
            car_grid_need = 0
            battery_grid_need = 0
            grid_setpoint_parts = []
        elif grid_setpoint < 0 and not export_permitted:
            gate_reason = "Grid export blocked - arbitrage dump not active"
            _LOGGER.info(
                "Grid setpoint safety net clamped %dW export to 0 "
                "(battery_grid_need=%d, arbitrage_mode_active=%s, "
                "battery_dump_export_power=%d)",
                int(grid_setpoint), int(battery_grid_need),
                ctx.arbitrage_mode_active, battery_dump_export_power,
            )
            grid_setpoint = 0
            car_grid_need = 0
            battery_grid_need = 0
            grid_setpoint_parts = []

        if gate_reason is not None:
            reason = f"{gate_reason} | {peak_context_reason}"
        elif not grid_setpoint_parts:
            reason = f"No grid charging needed | {peak_context_reason}"
        elif battery_dump_active and battery_grid_need < 0:
            components_text = " + ".join(grid_setpoint_parts)
            if grid_setpoint < 0:
                reason = (
                    f"Grid export scheduled for {components_text} = {int(abs(grid_setpoint))}W export"
                    f" | {ctx.arbitrage_mode_reason or 'Arbitrage mode active'}"
                )
            elif grid_setpoint == 0:
                reason = (
                    f"Battery export fully offset by local EV load ({components_text})"
                    f" | {ctx.arbitrage_mode_reason or 'Arbitrage mode active'}"
                )
            else:
                reason = (
                    f"Battery export netted against local EV load ({components_text}) = {int(grid_setpoint)}W import"
                    f" | {peak_context_reason}"
                )
        else:
            components_text = " + ".join(grid_setpoint_parts)
            if len(grid_setpoint_parts) == 1:
                reason = f"Grid import reserved for {components_text} | {peak_context_reason}"
            else:
                reason = (
                    f"Grid import reserved for {components_text} = {int(grid_setpoint)}W"
                    f" | {peak_context_reason}"
                )
        if solar_only_note:
            reason = f"{reason} | {solar_only_note}"

        return {
            "grid_setpoint": int(grid_setpoint),
            "grid_setpoint_reason": reason,
            "grid_components": {
                "battery": int(battery_grid_need),
                "car": int(car_grid_need),
            },
        }
