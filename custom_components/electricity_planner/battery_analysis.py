"""Battery status analysis and weighted-SOC calculation.

Extracted from ``decision_engine.py`` as a standalone collaborator. The
calculator consumes a list of raw battery SOC readings and returns the
aggregated analysis dictionary consumed by the rest of the decision
pipeline.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .defaults import DEFAULT_POWER_ESTIMATES

if TYPE_CHECKING:
    from .decision_engine import EngineSettings
    from .helpers import DataValidator

_LOGGER = logging.getLogger(__name__)


class BatteryAnalysisCalculator:
    """Analyze battery SOC data and compute capacity-weighted averages."""

    def __init__(self, settings: "EngineSettings", validator: "DataValidator") -> None:
        self._settings = settings
        self._validator = validator

    def analyze(self, battery_soc_data: list[dict[str, Any]]) -> dict[str, Any]:
        """Analyze battery status for all configured batteries."""
        _, validation_msg = self._validator.validate_battery_data(battery_soc_data)

        if not battery_soc_data:
            return self._create_no_battery_result()

        # Filter valid batteries
        valid_batteries = [
            battery for battery in battery_soc_data
            if "soc" in battery and battery["soc"] is not None and 0 <= battery["soc"] <= 100
        ]

        if not valid_batteries:
            return self._create_unavailable_battery_result(len(battery_soc_data))

        # Calculate metrics
        soc_values = [battery["soc"] for battery in valid_batteries]
        min_soc = min(soc_values)
        max_soc = max(soc_values)

        # Calculate weighted average if capacities configured
        average_soc = self.calculate_weighted_average_soc(valid_batteries)

        min_threshold = self._settings.min_soc_threshold
        max_threshold = self._settings.max_soc_threshold

        return {
            "average_soc": average_soc,
            "min_soc": min_soc,
            "max_soc": max_soc,
            "batteries_count": len(soc_values),
            "batteries_full": average_soc >= max_threshold,
            "min_soc_threshold": min_threshold,
            "max_soc_threshold": max_threshold,
            "remaining_capacity_percent": max_threshold - average_soc,
            "batteries_available": True,
            "validation_status": validation_msg,
            "capacity_weighted": bool(self._settings.battery_capacities),
        }

    def calculate_weighted_average_soc(self, batteries: list[dict[str, Any]]) -> float:
        """Calculate capacity-weighted average SOC."""
        if not batteries:
            _LOGGER.warning("Empty batteries list passed to calculate_weighted_average_soc")
            return 0.0

        capacities = self._settings.battery_capacities

        if not capacities:
            # Simple average
            return sum(b["soc"] for b in batteries) / len(batteries)

        # Weighted average
        total_energy = 0.0
        total_capacity = 0.0

        for battery in batteries:
            entity_id = battery["entity_id"]
            soc = battery["soc"]
            capacity = capacities.get(entity_id, DEFAULT_POWER_ESTIMATES.default_battery_capacity)

            energy = (soc / 100.0) * capacity
            total_energy += energy
            total_capacity += capacity

            _LOGGER.debug(
                "Battery %s: SOC=%.1f%%, Capacity=%.1fkWh, Stored=%.2fkWh",
                entity_id, soc, capacity, energy
            )

        if total_capacity > 0:
            return (total_energy / total_capacity) * 100.0

        return sum(b["soc"] for b in batteries) / len(batteries)

    def _create_no_battery_result(self) -> dict[str, Any]:
        """Create result when no batteries configured."""
        return {
            "average_soc": None,
            "min_soc": None,
            "max_soc": None,
            "batteries_count": 0,
            "batteries_full": False,
            "batteries_available": False,
            "validation_status": "No battery entities configured",
        }

    def _create_unavailable_battery_result(self, count: int) -> dict[str, Any]:
        """Create result when all batteries unavailable."""
        return {
            "average_soc": None,
            "min_soc": None,
            "max_soc": None,
            "batteries_count": count,
            "batteries_full": False,
            "batteries_available": False,
            "validation_status": "All battery SOC sensors unavailable",
        }
