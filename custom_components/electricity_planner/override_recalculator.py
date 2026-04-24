"""Manual-override decision recalculation.

Extracted from ``decision_engine.py``. After manual overrides adjust
charging flags, charger limit, or grid setpoint, this collaborator refreshes
the dependent values so that the decision remains internally consistent.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .const import PHASE_MODE_THREE

if TYPE_CHECKING:
    from .decision_engine import ChargingDecisionEngine, CycleContext

_LOGGER = logging.getLogger(__name__)


def _safe_optional_float(value: Any) -> float | None:
    """Best-effort conversion of arbitrary input to float."""
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class OverrideRecalculator:
    """Refresh dependent fields after manual overrides are applied."""

    def __init__(self, engine: "ChargingDecisionEngine") -> None:
        self._engine = engine

    @property
    def _settings(self):
        return self._engine._settings

    def normalize_car_override_state(self, data: dict[str, Any]) -> None:
        """Reset derived car charging flags after a manual ON/OFF override."""
        if not data.get("car_grid_charging", False):
            data["car_solar_only"] = False
            data["car_grid_import_allowed"] = False
            return

        data["car_solar_only"] = False
        data["car_grid_import_allowed"] = True

    def normalize_grid_components(
        self,
        decision: dict[str, Any],
        ctx: "CycleContext | None" = None,
    ) -> dict[str, int]:
        """Return a battery/car split consistent with the current grid setpoint."""
        from .decision_engine import CycleContext

        if ctx is None:
            price_analysis = decision.get("price_analysis") or {}
            if not isinstance(price_analysis, dict):
                price_analysis = {}

            battery_analysis = decision.get("battery_analysis") or {}
            if not isinstance(battery_analysis, dict):
                battery_analysis = {}

            power_analysis = decision.get("power_analysis") or {}
            if not isinstance(power_analysis, dict):
                power_analysis = {}

            power_allocation = decision.get("power_allocation") or {}
            if not isinstance(power_allocation, dict):
                power_allocation = {}

            ctx = CycleContext.from_data(
                decision,
                self._settings,
                battery_analysis,
                price_analysis,
                power_analysis,
                power_allocation,
            )

        total_grid_setpoint = int(_safe_optional_float(decision.get("grid_setpoint")) or 0)
        if total_grid_setpoint == 0:
            return {"battery": 0, "car": 0}

        grid_components = decision.get("grid_components")
        raw_battery = None
        raw_car = None
        if isinstance(grid_components, dict):
            raw_battery = _safe_optional_float(grid_components.get("battery"))
            raw_car = _safe_optional_float(grid_components.get("car"))

        if total_grid_setpoint > 0:
            weights: dict[str, float] = {}
            if raw_battery is not None and raw_battery > 0:
                weights["battery"] = raw_battery
            if raw_car is not None and raw_car > 0:
                weights["car"] = raw_car

            if weights:
                allocations = self._engine._distribute_quantity(
                    total_grid_setpoint,
                    list(weights),
                    weights,
                )
                return {
                    "battery": int(allocations.get("battery", 0)),
                    "car": int(allocations.get("car", 0)),
                }

            battery_enabled = ctx.battery_grid_charging
            car_enabled = ctx.car_grid_charging

            if battery_enabled and not car_enabled:
                return {"battery": total_grid_setpoint, "car": 0}
            if car_enabled and not battery_enabled:
                return {"battery": 0, "car": total_grid_setpoint}
            if battery_enabled and car_enabled:
                charger_limit = max(0, int(ctx.charger_limit))
                if charger_limit > 0:
                    car_allocation = min(charger_limit, total_grid_setpoint)
                    return {
                        "battery": total_grid_setpoint - car_allocation,
                        "car": car_allocation,
                    }

            return {"battery": total_grid_setpoint, "car": 0}

        return {"battery": total_grid_setpoint, "car": 0}

    @staticmethod
    def format_manual_grid_setpoint_reason(
        decision: dict[str, Any],
        grid_components: dict[str, int],
    ) -> str:
        """Describe the effective split of a manual grid-setpoint override."""
        manual_reason = (
            decision.get("manual_overrides", {})
            .get("grid_setpoint", {})
            .get("reason", "Manual grid setpoint override")
        )
        total_setpoint = int(_safe_optional_float(decision.get("grid_setpoint")) or 0)
        battery_component = int(grid_components.get("battery", 0))
        car_component = int(grid_components.get("car", 0))

        if total_setpoint < 0:
            direction = f"{abs(total_setpoint)}W export"
        elif total_setpoint > 0:
            direction = f"{total_setpoint}W import"
        else:
            direction = "0W neutral flow"

        return (
            f"Manual override: {manual_reason} "
            f"({direction}; battery {battery_component}W, car {car_component}W)"
        )

    def recalculate_after_override(
        self,
        baseline_data: dict[str, Any],
        decision: dict[str, Any],
        override_targets: set[str],
    ) -> dict[str, Any]:
        """Refresh dependent power limits after manual overrides adjust charging flags."""
        from .decision_engine import CycleContext

        if not override_targets:
            return decision

        price_analysis = decision.get("price_analysis") or {}
        if not isinstance(price_analysis, dict):
            price_analysis = {}

        battery_analysis = decision.get("battery_analysis") or {}
        if not isinstance(battery_analysis, dict):
            battery_analysis = {}

        power_analysis = decision.get("power_analysis") or {}
        if not isinstance(power_analysis, dict):
            power_analysis = {}

        power_allocation = decision.get("power_allocation") or {}
        if not isinstance(power_allocation, dict):
            power_allocation = {}

        combined_data: dict[str, Any] = {}
        if isinstance(baseline_data, dict):
            combined_data.update(baseline_data)
        combined_data.update(decision)

        if "car_grid_charging" in override_targets:
            self.normalize_car_override_state(decision)
            self.normalize_car_override_state(combined_data)

        # When the user force-enables battery grid charging the arbitrage export
        # window must not dominate.  Clear the dump flags so that
        # _calculate_grid_setpoint follows the charging path instead of the
        # export path.
        if "battery_grid_charging" in override_targets and decision.get("battery_grid_charging"):
            combined_data["arbitrage_mode_active"] = False
            combined_data["battery_dump_export_power"] = 0

        ctx = CycleContext.from_data(
            combined_data,
            self._settings,
            battery_analysis,
            price_analysis,
            power_analysis,
            power_allocation,
        )

        if (
            "charger_limit" not in override_targets
            and override_targets.intersection({"battery_grid_charging", "car_grid_charging"})
        ):
            charger_limit_decision = self._engine._calculate_charger_limit(
                price_analysis,
                battery_analysis,
                power_allocation,
                combined_data,
                ctx=ctx,
            )
            decision.update(charger_limit_decision)
            combined_data.update(charger_limit_decision)
            ctx = CycleContext.from_data(
                combined_data,
                self._settings,
                battery_analysis,
                price_analysis,
                power_analysis,
                power_allocation,
            )

        if "grid_setpoint" in override_targets:
            normalized_grid_components = self.normalize_grid_components(decision, ctx)
            decision["grid_components"] = normalized_grid_components
            combined_data["grid_components"] = normalized_grid_components
            decision["grid_setpoint_reason"] = self.format_manual_grid_setpoint_reason(
                decision,
                normalized_grid_components,
            )
            combined_data["grid_setpoint_reason"] = decision["grid_setpoint_reason"]

        if (
            "grid_setpoint" not in override_targets
            and override_targets.intersection({"battery_grid_charging", "car_grid_charging", "charger_limit"})
        ):
            charger_limit = decision.get("charger_limit", 0)
            grid_setpoint_decision = self._engine._calculate_grid_setpoint(
                price_analysis,
                battery_analysis,
                power_allocation,
                combined_data,
                charger_limit,
                ctx=ctx,
            )
            decision.update(grid_setpoint_decision)
            combined_data.update(grid_setpoint_decision)

        if (
            combined_data.get("phase_mode") == PHASE_MODE_THREE
            and combined_data.get("phase_details")
            and override_targets.intersection(
                {"battery_grid_charging", "car_grid_charging", "charger_limit", "grid_setpoint"}
            )
        ):
            phase_results = self._engine._distribute_phase_decisions(decision, combined_data)
            decision["phase_results"] = phase_results

        return decision
