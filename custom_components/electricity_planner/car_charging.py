"""Car charging decision calculation.

Extracted from ``decision_engine.py``. Holds the hysteresis/permissive logic
that decides whether the EV should charge from the grid (or run solar-only)
across the three price bands: very-low, low and high. The engine retains
thin delegators for each method so existing tests keep calling into the
same symbols.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .const import PERMISSIVE_MULTIPLIER_MIN, PERMISSIVE_MULTIPLIER_MAX

if TYPE_CHECKING:
    from .decision_engine import (
        CarChargingDecision,
        CarDecisionContext,
        CycleContext,
        EngineSettings,
    )

_LOGGER = logging.getLogger(__name__)

# Mirrors the constant in decision_engine.py — kept here to avoid a
# circular import. Both files must stay in sync.
_CAR_CHARGING_LOCKED_THRESHOLD_KEY = "car_charging_locked_threshold"


class CarChargingDecisionCalculator:
    """Make per-cycle car grid-charging decisions with hysteresis."""

    def __init__(self, settings: "EngineSettings") -> None:
        self._settings = settings

    # --- entry point -----------------------------------------------------

    def decide(
        self,
        engine: Any,
        price_analysis: dict[str, Any],
        battery_analysis: dict[str, Any],
        power_allocation: dict[str, Any],
        data: dict[str, Any],
        ctx: "CycleContext",
    ) -> dict[str, Any]:
        """Return the car grid-charging decision for this cycle."""
        if not ctx.has_price_data:
            return {
                "car_grid_charging": False,
                "car_grid_import_allowed": False,
                "car_grid_charging_reason": "No price data available",
            }

        context = engine._build_car_decision_context(price_analysis, ctx)
        if context.very_low_price and context.effective_low_price:
            base_decision = engine._car_decision_for_very_low_price(context, ctx, data)
        elif (
            context.is_low_price_flag
            or (context.permissive_mode_active and context.effective_low_price)
            or (context.previous_charging and context.effective_low_price)
        ):
            base_decision = engine._car_decision_for_low_price(context, ctx, data)
        else:
            base_decision = engine._car_decision_for_high_price(context, ctx, data)

        charging_allowed = bool(base_decision.get("car_grid_charging", False))
        solar_only = bool(base_decision.get("car_solar_only", False))
        grid_import_allowed = charging_allowed and not solar_only

        arbitrage_power = ctx.car_arbitrage_power
        if arbitrage_power > 0:
            grid_import_allowed = bool(
                grid_import_allowed
                or price_analysis.get("is_low_price")
                or context.very_low_price
            )
            source_parts = [f"up to {arbitrage_power}W battery arbitrage"]
            if context.has_allocated_solar:
                source_parts.insert(0, f"{context.format_solar_watts()} solar")
            if grid_import_allowed:
                source_parts.append("allowed grid import")
            reason = (
                f"Arbitrage mode active - charging allowed with {' + '.join(source_parts)}"
            )
            return {
                "car_grid_charging": True,
                "car_grid_import_allowed": grid_import_allowed,
                "car_grid_charging_reason": self.append_permissive_mode_to_reason(
                    reason, context
                ),
            }

        base_decision["car_grid_import_allowed"] = grid_import_allowed
        return base_decision

    # --- helpers ---------------------------------------------------------

    def build_context(
        self,
        price_analysis: dict[str, Any],
        ctx: "CycleContext",
    ) -> "CarDecisionContext":
        """Collect immutable inputs for the car charging decision."""
        from .decision_engine import CarDecisionContext

        base_threshold = ctx.resolved_price_threshold
        permissive_multiplier = ctx.car_permissive_multiplier
        effective_threshold = self.resolve_threshold(ctx)
        current_price = ctx.current_price

        return CarDecisionContext(
            current_price=current_price,
            base_threshold=base_threshold,
            effective_threshold=effective_threshold,
            previous_charging=ctx.previous_car_charging,
            has_min_window=ctx.has_min_charging_window,
            min_duration=self._settings.min_car_charging_duration,
            allocated_solar=ctx.allocated_car_solar,
            very_low_price=bool(price_analysis.get("very_low_price")),
            very_low_percent=float(self._settings.very_low_price_threshold_pct),
            is_low_price_flag=bool(price_analysis.get("is_low_price")),
            effective_low_price=(
                current_price is not None and current_price <= effective_threshold
            ),
            permissive_mode_active=ctx.car_permissive_mode_active,
            permissive_multiplier=permissive_multiplier,
        )

    def resolve_threshold(self, ctx: "CycleContext") -> float:
        """Apply hysteresis threshold floor and permissive multiplier."""
        if ctx.previous_car_charging and ctx.locked_car_threshold is not None:
            _LOGGER.debug(
                "Car charging active: using threshold floor %.4f€/kWh (locked=%.4f€/kWh, current=%.4f€/kWh)",
                ctx.car_threshold_floor,
                ctx.locked_car_threshold,
                ctx.resolved_price_threshold,
            )

        if ctx.car_permissive_mode_active and ctx.car_permissive_multiplier > 1.0:
            if ctx.effective_car_permissive_multiplier != ctx.car_permissive_multiplier:
                _LOGGER.warning(
                    "Permissive multiplier %.2f outside safe range [%.1f, %.1f], clamping to %.2f",
                    ctx.car_permissive_multiplier, PERMISSIVE_MULTIPLIER_MIN,
                    PERMISSIVE_MULTIPLIER_MAX, ctx.effective_car_permissive_multiplier
                )

            _LOGGER.debug(
                "Permissive mode active: threshold %.4f€/kWh → %.4f€/kWh (+%.0f%%)",
                ctx.car_threshold_floor,
                ctx.effective_car_price_threshold,
                (ctx.effective_car_permissive_multiplier - 1) * 100,
            )
            return ctx.effective_car_price_threshold

        return ctx.effective_car_price_threshold

    @staticmethod
    def lock_threshold(ctx: "CycleContext", data: dict[str, Any]) -> None:
        """Lock the price threshold when starting car charging (OFF→ON)."""
        data[_CAR_CHARGING_LOCKED_THRESHOLD_KEY] = ctx.resolved_price_threshold
        _LOGGER.debug(
            "Car charging starting: locking threshold at %.4f€/kWh",
            ctx.resolved_price_threshold,
        )

    @staticmethod
    def unlock_threshold(_ctx: "CycleContext", data: dict[str, Any]) -> None:
        """Clear the locked threshold when stopping car charging (ON→OFF)."""
        data[_CAR_CHARGING_LOCKED_THRESHOLD_KEY] = None
        _LOGGER.debug("Car charging stopping: clearing locked threshold")

    @staticmethod
    def append_solar_info_to_reason(
        reason: str, context: "CarDecisionContext"
    ) -> str:
        if context.has_allocated_solar:
            return f"{reason}, solar available ({context.format_solar_watts()})"
        return reason

    @staticmethod
    def append_permissive_mode_to_reason(
        reason: str, context: "CarDecisionContext"
    ) -> str:
        if context.permissive_mode_active and context.permissive_multiplier > 1.0:
            increase_pct = (context.permissive_multiplier - 1.0) * 100
            return f"{reason} [Permissive: +{increase_pct:.0f}%]"
        return reason

    @staticmethod
    def format_high_price_reason(context: "CarDecisionContext") -> str:
        return f"Price too high ({context.format_price_comparison('>')})"

    def build_reason_with_solar(
        self,
        base_reason: str,
        context: "CarDecisionContext",
        include_solar_inline: bool = False,
    ) -> str:
        if include_solar_inline and context.has_allocated_solar:
            reason = f"{base_reason} with solar ({context.format_solar_watts()})"
        elif context.has_allocated_solar:
            reason = self.append_solar_info_to_reason(base_reason, context)
        else:
            reason = base_reason
        return self.append_permissive_mode_to_reason(reason, context)

    # --- per-band decisions ---------------------------------------------

    def decision_for_very_low_price(
        self,
        context: "CarDecisionContext",
        ctx: "CycleContext",
        data: dict[str, Any],
    ) -> "CarChargingDecision":
        price = context.display_price

        if context.previous_charging:
            base_reason = (
                f"Very low price ({price:.3f}€/kWh) - bottom "
                f"{context.very_low_percent}% of daily range (continuing)"
            )
            reason = self.build_reason_with_solar(
                base_reason, context, include_solar_inline=True
            )
            return {"car_grid_charging": True, "car_grid_charging_reason": reason}
        elif context.has_min_window:
            self.lock_threshold(ctx, data)
            base_reason = (
                f"Very low price ({price:.3f}€/kWh) - bottom "
                f"{context.very_low_percent}% of daily range ({context.min_duration}h+ window available)"
            )
            reason = self.build_reason_with_solar(
                base_reason, context, include_solar_inline=True
            )
            return {"car_grid_charging": True, "car_grid_charging_reason": reason}
        else:
            if context.has_allocated_solar:
                base_reason = (
                    f"Very low price ({price:.3f}€/kWh) but less than {context.min_duration}h "
                    f"of low prices ahead - using solar power only ({context.format_solar_watts()})"
                )
                return {
                    "car_grid_charging": True,
                    "car_solar_only": True,
                    "car_grid_charging_reason": self.append_permissive_mode_to_reason(
                        base_reason, context
                    ),
                }
            base_reason = (
                f"Very low price ({price:.3f}€/kWh) but less than {context.min_duration}h "
                "of low prices ahead - waiting for longer window"
            )
            return {
                "car_grid_charging": False,
                "car_grid_charging_reason": self.append_permissive_mode_to_reason(
                    base_reason, context
                ),
            }

    def decision_for_low_price(
        self,
        context: "CarDecisionContext",
        ctx: "CycleContext",
        data: dict[str, Any],
    ) -> "CarChargingDecision":
        if context.previous_charging:
            base_reason = f"Low price ({context.format_price_comparison()}) - continuing"
            reason = self.build_reason_with_solar(base_reason, context, include_solar_inline=True)
            return {"car_grid_charging": True, "car_grid_charging_reason": reason}

        if context.has_min_window:
            self.lock_threshold(ctx, data)
            base_reason = (
                f"Low price ({context.format_price_comparison()}), "
                f"{context.min_duration}h+ window available - starting"
            )
            reason = self.build_reason_with_solar(base_reason, context, include_solar_inline=True)
            return {"car_grid_charging": True, "car_grid_charging_reason": reason}

        if context.is_low_price_flag:
            if context.has_allocated_solar:
                base_reason = (
                    f"Low price ({context.format_price_comparison()}) but less than {context.min_duration}h "
                    f"of low prices ahead - using solar power only ({context.format_solar_watts()})"
                )
                return {
                    "car_grid_charging": True,
                    "car_solar_only": True,
                    "car_grid_charging_reason": self.append_permissive_mode_to_reason(base_reason, context),
                }
            base_reason = (
                f"Low price ({context.format_price_comparison()}) but less than {context.min_duration}h "
                "of low prices ahead - waiting for longer window"
            )
            return {
                "car_grid_charging": False,
                "car_grid_charging_reason": self.append_permissive_mode_to_reason(base_reason, context),
            }

        window_requirement = (
            f"needs ≤ {context.effective_threshold:.3f}€/kWh for ≥ {context.min_duration}h - "
            "current forecast shorter"
        )
        if context.permissive_mode_active and context.permissive_multiplier > 1.0:
            window_requirement += f" (base {context.base_threshold:.3f}€/kWh)"

        base_reason = (
            f"Waiting for low-price window before starting "
            f"({context.format_price_comparison()} floor, {window_requirement})"
        )
        if context.has_allocated_solar:
            solar_reason = (
                f"Waiting for low-price window before starting "
                f"({context.format_price_comparison()} floor, {window_requirement}) - "
                f"using solar power only ({context.format_solar_watts()})"
            )
            return {
                "car_grid_charging": True,
                "car_solar_only": True,
                "car_grid_charging_reason": self.append_permissive_mode_to_reason(solar_reason, context),
            }
        return {
            "car_grid_charging": False,
            "car_grid_charging_reason": self.append_permissive_mode_to_reason(base_reason, context),
        }

    def decision_for_high_price(
        self,
        context: "CarDecisionContext",
        ctx: "CycleContext",
        data: dict[str, Any],
    ) -> "CarChargingDecision":
        high_price_reason = self.format_high_price_reason(context)

        if context.previous_charging:
            self.unlock_threshold(ctx, data)
            if context.has_allocated_solar:
                base_reason = (
                    f"{high_price_reason} - switching to solar power only ({context.format_solar_watts()})"
                )
                return {
                    "car_grid_charging": True,
                    "car_solar_only": True,
                    "car_grid_charging_reason": self.append_permissive_mode_to_reason(base_reason, context),
                }
            base_reason = f"{high_price_reason} - stopping car charging"
            return {
                "car_grid_charging": False,
                "car_grid_charging_reason": self.append_permissive_mode_to_reason(base_reason, context),
            }

        if context.has_allocated_solar:
            base_reason = (
                f"{high_price_reason} - "
                f"using allocated solar power only ({context.format_solar_watts()})"
            )
            return {
                "car_grid_charging": True,
                "car_solar_only": True,
                "car_grid_charging_reason": self.append_permissive_mode_to_reason(base_reason, context),
            }

        return {
            "car_grid_charging": False,
            "car_grid_charging_reason": self.append_permissive_mode_to_reason(high_price_reason, context),
        }
