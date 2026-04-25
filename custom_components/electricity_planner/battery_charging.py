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

    def refresh(
        self, settings: "EngineSettings", config: dict[str, Any]
    ) -> None:
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

        # NOTE: Arbitrage mode does NOT block battery grid charging here.
        # The normal price-based strategies decide whether to charge.
        # Only the explicit "Disable Battery Charging" switch (manual override
        # with value=False on battery_grid_charging) should prevent grid charging.
        # During active arbitrage export windows the grid-setpoint calculation
        # gives export priority over charging, so grid charging is naturally
        # suppressed without an extra block here.

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

        return {
            "battery_grid_charging": should_charge,
            "battery_grid_charging_reason": reason,
            "strategy_trace": trace,
        }
