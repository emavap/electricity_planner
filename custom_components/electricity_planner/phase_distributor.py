"""Three-phase decision distribution.

Extracted from ``decision_engine.py``. Breaks down the aggregated
single-phase decision into per-phase guidance using capacity-weighted
battery allocations and equal-share car allocations.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from .const import PHASE_IDS

_LOGGER = logging.getLogger(__name__)


class PhaseDistributor:
    """Distribute aggregated decisions across the three phases."""

    def __init__(
        self,
        normalize_grid_components: Callable[[dict[str, Any]], dict[str, int]],
    ) -> None:
        self._normalize_grid_components = normalize_grid_components

    @staticmethod
    def distribute_quantity(
        total: int,
        phases: list[str],
        weights: dict[str, float],
    ) -> dict[str, int]:
        """Distribute an integer total across phases using weighted rounding."""
        if total == 0 or not phases:
            return {phase: 0 for phase in phases}

        sign = -1 if total < 0 else 1
        abs_total = abs(total)

        positive_weights = {phase: max(weights.get(phase, 0.0), 0.0) for phase in phases}
        weight_sum = sum(positive_weights.values())

        if weight_sum <= 0:
            base_share = abs_total // len(phases)
            remainder = abs_total - (base_share * len(phases))
            allocation = {phase: base_share for phase in phases}
            for phase in phases[:remainder]:
                allocation[phase] += 1
            return {phase: allocation[phase] * sign for phase in phases}

        raw_allocations = {
            phase: (abs_total * positive_weights[phase] / weight_sum) for phase in phases
        }
        allocation = {phase: int(raw_allocations[phase] // 1) for phase in phases}
        remainder = int(abs_total - sum(allocation.values()))

        if remainder > 0:
            fractional_order = sorted(
                phases,
                key=lambda phase: raw_allocations[phase] - allocation[phase],
                reverse=True,
            )
            for phase in fractional_order[:remainder]:
                allocation[phase] += 1

        return {phase: allocation[phase] * sign for phase in phases}

    def distribute_phase_decisions(
        self,
        overall_decision: dict[str, Any],
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Break down the aggregated decision into per-phase guidance."""
        phase_details: dict[str, dict[str, Any]] = data.get("phase_details") or {}
        if not phase_details:
            _LOGGER.warning(
                "Three-phase mode active but no phase details available - "
                "check that at least one sensor is configured for each phase"
            )
            return {}

        ordered_phases = [phase for phase in PHASE_IDS if phase in phase_details]
        if not ordered_phases:
            ordered_phases = list(phase_details.keys())

        phase_capacity_map: dict[str, float] = data.get("phase_capacity_map", {})
        phase_batteries: dict[str, list[dict[str, Any]]] = data.get("phase_batteries", {})
        total_capacity_weight = sum(
            max(phase_capacity_map.get(phase, 0.0), 0.0) for phase in ordered_phases
        )

        grid_components = self._normalize_grid_components(overall_decision)
        battery_component = int(grid_components.get("battery", 0) or 0)
        car_component = int(grid_components.get("car", 0) or 0)

        battery_phases = [
            phase for phase in ordered_phases if phase_batteries.get(phase)
        ]
        battery_weight_map = {
            phase: max(phase_capacity_map.get(phase, 0.0), 0.0)
            for phase in ordered_phases
        }
        if not battery_phases and battery_component != 0:
            battery_phases = ordered_phases

        battery_allocations = (
            self.distribute_quantity(
                battery_component,
                battery_phases,
                {phase: battery_weight_map.get(phase, 0.0) for phase in battery_phases},
            )
            if battery_component != 0 and battery_phases
            else {phase: 0 for phase in ordered_phases}
        )

        car_weight_map: dict[str, float] = {}
        car_phases: list[str] = []
        for phase in ordered_phases:
            details = phase_details.get(phase, {})
            car_power = details.get("car_charging_power")
            has_car = details.get("has_car_sensor") or (car_power is not None)
            if has_car:
                car_phases.append(phase)
                car_weight_map[phase] = 1.0

        if car_component > 0 and not car_phases:
            car_phases = ordered_phases
            car_weight_map = {phase: 1.0 for phase in ordered_phases}

        car_allocations = (
            self.distribute_quantity(
                car_component,
                car_phases,
                {phase: car_weight_map.get(phase, 0.0) for phase in car_phases},
            )
            if car_component > 0 and car_phases
            else {phase: 0 for phase in ordered_phases}
        )

        charger_limit_total = int(overall_decision.get("charger_limit", 0) or 0)
        if charger_limit_total > 0 and not car_phases:
            car_phases = ordered_phases
            car_weight_map = {phase: 1.0 for phase in ordered_phases}

        charger_allocations = (
            self.distribute_quantity(
                charger_limit_total,
                car_phases,
                {phase: car_weight_map.get(phase, 0.0) for phase in car_phases},
            )
            if charger_limit_total > 0 and car_phases
            else {phase: 0 for phase in ordered_phases}
        )

        phase_results: dict[str, Any] = {}
        battery_reason = overall_decision.get("battery_grid_charging_reason")
        car_reason = overall_decision.get("car_grid_charging_reason")

        for phase in ordered_phases:
            grid_from_battery = battery_allocations.get(phase, 0)
            grid_from_car = car_allocations.get(phase, 0)
            grid_setpoint = grid_from_battery + grid_from_car

            has_battery = bool(phase_batteries.get(phase))
            battery_allowed = overall_decision.get("battery_grid_charging", False) and has_battery

            if overall_decision.get("battery_grid_charging", False) and not has_battery:
                phase_battery_reason = "No batteries assigned to this phase"
            else:
                phase_battery_reason = battery_reason

            car_enabled_globally = overall_decision.get("car_grid_charging", False)
            phase_has_car = phase in car_phases
            car_allowed = car_enabled_globally and phase_has_car

            if car_enabled_globally and not phase_has_car:
                phase_car_reason = "No EV feed configured for this phase"
            else:
                phase_car_reason = car_reason

            phase_results[phase] = {
                "grid_setpoint": int(grid_setpoint),
                "grid_components": {
                    "battery": int(grid_from_battery),
                    "car": int(grid_from_car),
                },
                "battery_grid_charging": bool(battery_allowed),
                "battery_grid_charging_reason": phase_battery_reason,
                "car_grid_charging": bool(car_allowed),
                "car_grid_charging_reason": phase_car_reason,
                "charger_limit": int(charger_allocations.get(phase, 0)),
                "battery_entities": [
                    battery["entity_id"] for battery in phase_batteries.get(phase, [])
                ],
                "capacity_share": (
                    phase_capacity_map.get(phase, 0.0) / total_capacity_weight
                    if total_capacity_weight > 0
                    else 0.0
                ),
                "capacity_share_kwh": phase_capacity_map.get(phase, 0.0),
            }

        return phase_results
