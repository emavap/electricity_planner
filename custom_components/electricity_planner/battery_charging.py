"""Battery grid-charging decision calculation.

Extracted from ``decision_engine.py``. Wraps the safety checks, solar
surplus short-circuit, and strategy-manager evaluation that together
decide whether batteries should be charged from the grid for a given
cycle. The engine retains a thin ``_decide_battery_grid_charging``
delegator so existing tests and monkeypatches keep working.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .const import DEFAULT_MAX_SOC

if TYPE_CHECKING:
    from .decision_engine import CycleContext, EngineSettings
    from .strategies import StrategyManager

_MIN_BATTERY_ON_HOLD_SECONDS = 5 * 60


class BatteryChargingDecisionCalculator:
    """Decide battery grid-charging using strategy pattern with hysteresis."""

    def __init__(
        self,
        settings: "EngineSettings",
        strategy_manager: "StrategyManager",
        config: dict[str, Any],
    ) -> None:
        self._settings = settings
        self._strategy_manager = strategy_manager
        self._config = config

    def refresh(self, settings: "EngineSettings", config: dict[str, Any]) -> None:
        self._settings = settings
        self._config = config

    def decide(
        self,
        price_analysis: dict[str, Any],
        battery_analysis: dict[str, Any],
        power_allocation: dict[str, Any],
        power_analysis: dict[str, Any],
        time_context: dict[str, Any],
        ctx: "CycleContext",
    ) -> dict[str, Any]:
        # Active arbitrage export is a hard automatic stop for the battery
        # automation binary. It must run before Negative Arbitrage Buy because
        # the grid-setpoint path also gives active export priority when both
        # runtime plans overlap.
        if ctx.arbitrage_mode_export_active:
            export_power = ctx.arbitrage_mode_export_power
            return {
                "battery_grid_charging": False,
                "battery_grid_charging_reason": (
                    f"Arbitrage export active ({export_power}W) - battery grid charging suppressed"
                ),
                "strategy_trace": [
                    {
                        "strategy": "ArbitrageExportGuard",
                        "priority": 0,
                        "should_charge": False,
                        "reason": (
                            f"Arbitrage export active ({export_power}W) - "
                            "battery grid charging suppressed"
                        ),
                    }
                ],
            }

        # Negative Arbitrage Buy is price-triggered by the planner. Once active,
        # request grid import even if the battery is already at a normal SOC
        # ceiling; the downstream inverter/battery system decides where that
        # imported power can go.
        if ctx.negative_buy_mode_active:
            current_price = ctx.current_price
            price_text = (
                f"{current_price:.3f}€/kWh" if current_price is not None else "n/a"
            )
            return {
                "battery_grid_charging": True,
                "battery_grid_charging_reason": (
                    f"Negative Arbitrage Buy active at {price_text} - requesting grid import"
                ),
                "strategy_trace": [],
            }

        if battery_analysis.get("batteries_count", 0) == 0:
            return {
                "battery_grid_charging": False,
                "battery_grid_charging_reason": "No battery entities configured",
                "strategy_trace": [],
            }

        if not battery_analysis.get("batteries_available", True):
            return {
                "battery_grid_charging": False,
                "battery_grid_charging_reason": battery_analysis.get(
                    "validation_status",
                    "Battery data unavailable",
                ),
                "strategy_trace": [],
            }

        if battery_analysis.get("batteries_full"):
            max_threshold = battery_analysis.get("max_soc_threshold", DEFAULT_MAX_SOC)
            average_soc = battery_analysis.get("average_soc", 0)
            return {
                "battery_grid_charging": False,
                "battery_grid_charging_reason": (
                    f"Battery average SOC {average_soc:.0f}% ≥ {max_threshold}% threshold"
                ),
                "strategy_trace": [],
            }

        if not ctx.has_price_data:
            return {
                "battery_grid_charging": False,
                "battery_grid_charging_reason": "No price data available",
                "strategy_trace": [],
            }

        significant_solar = power_analysis.get("significant_solar_surplus", False)
        solar_surplus = power_analysis.get("solar_surplus", 0)
        average_soc = battery_analysis.get("average_soc")
        surplus_block_soc = self._settings.max_soc_threshold_solar

        if (
            significant_solar
            and average_soc is not None
            and average_soc >= surplus_block_soc
        ):
            return {
                "battery_grid_charging": False,
                "battery_grid_charging_reason": (
                    f"Significant solar surplus ({solar_surplus:.0f}W) available - "
                    f"SOC {average_soc:.0f}% ≥ {surplus_block_soc}% so waiting for free solar "
                    f"(even at very low prices)"
                ),
                "strategy_trace": [],
            }

        context = {
            "price_analysis": price_analysis,
            "battery_analysis": battery_analysis,
            "power_allocation": power_allocation,
            "power_analysis": power_analysis,
            "time_context": time_context,
            "config": self._config,
            "settings": self._settings,
            "battery_stable_threshold": ctx.battery_stable_threshold,
            "effective_threshold": ctx.effective_battery_price_threshold,
            "soc_price_multiplier": ctx.soc_price_multiplier,
            "threshold_relaxed": ctx.threshold_relaxed,
        }

        should_charge, reason = self._strategy_manager.evaluate(context)
        trace = self._strategy_manager.get_last_trace()

        return self._apply_on_hold(
            {
                "battery_grid_charging": should_charge,
                "battery_grid_charging_reason": reason,
                "strategy_trace": trace,
            },
            ctx,
        )

    @staticmethod
    def _apply_on_hold(
        decision: dict[str, Any],
        ctx: "CycleContext",
    ) -> dict[str, Any]:
        """Avoid rapid ON->OFF cycling while the price remains acceptable.

        When an ON state was entered at a given price threshold, hold ON for
        up to ``_MIN_BATTERY_ON_HOLD_SECONDS`` while ``current_price`` stays
        at or below the **current** effective battery price threshold
        (base × SOC multiplier).

        The effective threshold is re-evaluated every cycle rather than frozen
        at hold-entry.  This prevents the hold from keeping charging ON when
        the underlying floor has changed (e.g. a ``battery_stable_threshold``
        relaxation deactivates mid-hold).  SOC drift during a 5-minute hold is
        negligible in practice, so the former locked-threshold guard against it
        is removed in favour of correctness under changing floors.
        """
        if decision.get("battery_grid_charging"):
            return decision

        if not ctx.previous_battery_grid_charging:
            return decision

        trace = list(decision.get("strategy_trace") or [])
        # SolarPriorityStrategy is wired at priority 1 with a non-empty reason
        # only when solar has actually taken over; that is a legitimate
        # OFF transition and must not be held.
        if any(entry.get("priority") == 1 and entry.get("reason") for entry in trace):
            return decision

        state_age = ctx.battery_grid_charging_state_age_seconds
        if state_age is None or state_age >= _MIN_BATTERY_ON_HOLD_SECONDS:
            return decision

        current_price = ctx.current_price
        if current_price is None:
            return decision

        # Use the current effective threshold (base × SOC multiplier,
        # including any active battery_stable_threshold) as the ceiling.
        # The locked threshold captured at OFF→ON is stale when the floor
        # changes (e.g. stable-threshold relaxation deactivates), and SOC
        # drift during a 5-minute hold is too small to matter.
        hold_threshold = ctx.effective_battery_price_threshold

        if current_price > hold_threshold:
            return decision

        remaining_seconds = max(0, int(_MIN_BATTERY_ON_HOLD_SECONDS - state_age))
        reason = (
            decision.get("battery_grid_charging_reason") or "automatic decision changed"
        )
        held = dict(decision)
        held["battery_grid_charging"] = True
        held["battery_grid_charging_reason"] = (
            "Continuing battery grid charging briefly to avoid rapid cycling "
            f"({remaining_seconds}s hold remaining, effective threshold "
            f"{hold_threshold:.3f}€/kWh); next automatic result: {reason}"
        )
        trace.append(
            {
                "strategy": "BatteryOnHold",
                "priority": 997,
                "should_charge": True,
                "reason": held["battery_grid_charging_reason"],
            }
        )
        held["strategy_trace"] = trace
        return held
