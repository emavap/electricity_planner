"""Battery status analysis and weighted-SOC calculation.

Extracted from ``decision_engine.py`` as a standalone collaborator. The
calculator consumes a list of raw battery SOC readings and returns the
aggregated analysis dictionary consumed by the rest of the decision
pipeline.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

from .const import BATTERY_SOC_HOLD_SECONDS
from .defaults import DEFAULT_POWER_ESTIMATES

if TYPE_CHECKING:
    from datetime import datetime

    from .decision_engine import EngineSettings
    from .helpers import DataValidator

_LOGGER = logging.getLogger(__name__)


class BatteryAnalysisCalculator:
    """Analyze battery SOC data and compute capacity-weighted averages."""

    def __init__(self, settings: "EngineSettings", validator: "DataValidator") -> None:
        self._settings = settings
        self._validator = validator
        # entity_id -> (soc, recorded_at). Lets a transient sensor dropout keep
        # contributing its last reading instead of being dropped from the fleet.
        self._soc_hold: dict[str, tuple[float, "datetime"]] = {}

    @property
    def soc_hold_state(self) -> dict[str, tuple[float, "datetime"]]:
        """Expose the held-SOC cache so it survives settings refreshes."""
        return self._soc_hold

    def restore_soc_hold_state(
        self, state: dict[str, tuple[float, "datetime"]] | None
    ) -> None:
        """Adopt a previously captured held-SOC cache."""
        if state:
            self._soc_hold = dict(state)

    def analyze(
        self,
        battery_soc_data: list[dict[str, Any]],
        _now: "datetime | None" = None,
    ) -> dict[str, Any]:
        """Analyze battery status for all configured batteries.

        ``_now`` overrides the clock used to age held SOC readings; it exists so
        tests can exercise the hold window deterministically.
        """
        _, validation_msg = self._validator.validate_battery_data(battery_soc_data)

        if not battery_soc_data:
            return self._create_no_battery_result()

        valid_batteries, held_entity_ids = self._resolve_batteries(
            battery_soc_data, _now
        )

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

        result = {
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

        if held_entity_ids:
            result["soc_held_entity_ids"] = held_entity_ids
            result["soc_hold_active"] = True

        return result

    def resolve_for_publication(
        self,
        battery_soc_data: list[dict[str, Any]],
        _now: "datetime | None" = None,
    ) -> list[dict[str, Any]]:
        """Return readings with held values substituted, unusable ones dropped.

        The coordinator fetches and publishes data before the decision engine
        runs ``analyze``, so this pass records valid readings too — otherwise the
        cache would always be one refresh behind and the very first dropout
        would have nothing to hold. ``analyze`` re-recording the same values
        later is harmless.
        """
        resolved, _ = self._resolve_batteries(battery_soc_data, _now)
        return resolved

    def _resolve_batteries(
        self,
        battery_soc_data: list[dict[str, Any]],
        now: "datetime | None" = None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Return usable battery readings, substituting recent held values.

        A battery whose SOC sensor is momentarily unavailable keeps contributing
        its last valid reading for ``BATTERY_SOC_HOLD_SECONDS``. Without this the
        fleet average silently re-weights onto the surviving batteries, which can
        cross a SOC threshold and flip charging decisions even though no charge
        actually moved.
        """
        now = now or dt_util.utcnow()
        resolved: list[dict[str, Any]] = []
        held_entity_ids: list[str] = []

        for battery in battery_soc_data:
            entity_id = battery.get("entity_id")
            soc = battery.get("soc")

            # Publication substitutes held readings before decision analysis.
            # They must pass through the original cache/age check rather than
            # being recorded as fresh telemetry on every refresh.
            if not battery.get("soc_held") and soc is not None and 0 <= soc <= 100:
                if entity_id:
                    self._soc_hold[entity_id] = (soc, now)
                resolved.append(battery)
                continue

            held = self._soc_hold.get(entity_id) if entity_id else None
            if held is None:
                continue

            held_soc, recorded_at = held
            age = (now - recorded_at).total_seconds()
            if age < 0 or age > BATTERY_SOC_HOLD_SECONDS:
                self._soc_hold.pop(entity_id, None)
                continue

            _LOGGER.debug(
                "Battery %s unavailable; holding last SOC %.1f%% (age %.0fs)",
                entity_id,
                held_soc,
                age,
            )
            resolved.append({**battery, "soc": held_soc, "soc_held": True})
            held_entity_ids.append(entity_id)

        return resolved, held_entity_ids

    def calculate_weighted_average_soc(self, batteries: list[dict[str, Any]]) -> float:
        """Calculate capacity-weighted average SOC."""
        if not batteries:
            _LOGGER.warning(
                "Empty batteries list passed to calculate_weighted_average_soc"
            )
            return 0.0

        capacities = self._settings.battery_capacities

        if not capacities:
            # Simple average
            return sum(b["soc"] for b in batteries) / len(batteries)

        # Weighted average
        total_energy = 0.0
        total_capacity = 0.0

        for battery in batteries:
            entity_id = battery.get("entity_id")
            soc = battery["soc"]
            capacity = capacities.get(
                entity_id, DEFAULT_POWER_ESTIMATES.default_battery_capacity
            )

            energy = (soc / 100.0) * capacity
            total_energy += energy
            total_capacity += capacity

            _LOGGER.debug(
                "Battery %s: SOC=%.1f%%, Capacity=%.1fkWh, Stored=%.2fkWh",
                entity_id,
                soc,
                capacity,
                energy,
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
