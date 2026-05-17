"""Minimum charging-window validator.

Extracted from ``coordinator.py`` as a standalone collaborator. Evaluates
whether the upcoming price timeline contains a contiguous low-price window
long enough to justify starting a new car charging session.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

from .const import (
    CONF_MIN_CAR_CHARGING_DURATION,
    CONF_PRICE_THRESHOLD,
    DEFAULT_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
    DEFAULT_MIN_CAR_CHARGING_DURATION,
    DEFAULT_PRICE_THRESHOLD,
    PRICE_INTERVAL_GAP_TOLERANCE_SECONDS,
)

if TYPE_CHECKING:
    from .coordinator import ElectricityPlannerCoordinator

_LOGGER = logging.getLogger(__name__)


class ChargingWindowValidator:
    """Validate whether a minimum low-price charging window exists from now."""

    def __init__(self, coordinator: "ElectricityPlannerCoordinator") -> None:
        self._coordinator = coordinator

    def check(
        self,
        prices_today: dict[str, Any] | None,
        prices_tomorrow: dict[str, Any] | None,
        transport_lookup: list[dict[str, Any]] | None,
        current_transport_cost: float | None,
        average_threshold: float | None = None,
        permissive_mode_active: bool = False,
        permissive_multiplier: float = DEFAULT_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
    ) -> bool:
        """Return True if a minimum low-price charging window exists from now."""
        coordinator = self._coordinator
        if not prices_today and not prices_tomorrow:
            return False

        now = dt_util.utcnow()

        if coordinator._should_use_average_threshold(average_threshold):
            base_threshold = average_threshold
        else:
            base_threshold = coordinator.config.get(
                CONF_PRICE_THRESHOLD, DEFAULT_PRICE_THRESHOLD
            )

        try:
            multiplier_value = float(permissive_multiplier)
        except (TypeError, ValueError):
            _LOGGER.warning(
                "Invalid car permissive threshold multiplier %s, falling back to 1.0",
                permissive_multiplier,
            )
            multiplier_value = 1.0

        effective_threshold = base_threshold
        permissive_window_enabled = False
        if permissive_mode_active and multiplier_value > 1.0:
            effective_threshold = base_threshold * multiplier_value
            permissive_window_enabled = True

        timeline = coordinator._build_price_timeline(
            prices_today,
            prices_tomorrow,
            transport_lookup,
            current_transport_cost,
            now,
        )

        if not timeline:
            return False

        coordinator._last_price_timeline = timeline
        coordinator._last_price_timeline_generated_at = now
        coordinator._last_price_timeline_data_hash = (
            coordinator._compute_price_data_hash(prices_today, prices_tomorrow)
        )

        current_idx: int | None = None
        for idx, (start_time, interval_end, _) in enumerate(timeline):
            if interval_end <= now:
                continue
            if start_time <= now < interval_end:
                current_idx = idx
                break

        if current_idx is None:
            _LOGGER.debug(
                "No active Nord Pool interval covering current time %s", now.isoformat()
            )
            return False

        min_duration_hours = coordinator.config.get(
            CONF_MIN_CAR_CHARGING_DURATION, DEFAULT_MIN_CAR_CHARGING_DURATION
        )
        required_duration = timedelta(hours=min_duration_hours)

        current_start, current_end, current_price = timeline[current_idx]
        if current_price > effective_threshold:
            threshold_note = (
                f"{'permissive ' if permissive_window_enabled else ''}threshold "
                f"{effective_threshold:.4f} €/kWh (base {base_threshold:.4f} €/kWh)"
            )
            _LOGGER.debug(
                "Current price %.4f €/kWh exceeds %s - no charging window starting now",
                current_price,
                threshold_note,
            )
            return False

        low_price_duration = current_end - max(now, current_start)
        if low_price_duration >= required_duration:
            threshold_note = (
                f"≤ {effective_threshold:.4f} €/kWh "
                f"(base {base_threshold:.4f} €/kWh)"
                if permissive_window_enabled
                else f"≤ {effective_threshold:.4f} €/kWh"
            )
            _LOGGER.debug(
                "Found %d-hour charging window entirely within current interval (price %.4f €/kWh %s)",
                min_duration_hours,
                current_price,
                threshold_note,
            )
            return True

        previous_end = current_end
        for next_idx in range(current_idx + 1, len(timeline)):
            next_start, next_end, next_price = timeline[next_idx]

            if next_start > previous_end + timedelta(
                seconds=PRICE_INTERVAL_GAP_TOLERANCE_SECONDS
            ):
                _LOGGER.debug(
                    "Charging window broken by gap between %s and %s",
                    previous_end.isoformat(),
                    next_start.isoformat(),
                )
                break

            if next_price > effective_threshold:
                threshold_note = (
                    f"{'permissive ' if permissive_window_enabled else ''}threshold "
                    f"{effective_threshold:.4f} €/kWh (base {base_threshold:.4f} €/kWh)"
                )
                _LOGGER.debug(
                    "Charging window broken by high price %.4f €/kWh (> %s)",
                    next_price,
                    threshold_note,
                )
                break

            effective_start = max(previous_end, next_start)
            if next_end <= effective_start:
                continue

            low_price_duration += next_end - effective_start
            previous_end = max(previous_end, next_end)

            if low_price_duration >= required_duration:
                threshold_note = (
                    f"≤ {effective_threshold:.4f} €/kWh "
                    f"(base {base_threshold:.4f} €/kWh)"
                    if permissive_window_enabled
                    else f"≤ {effective_threshold:.4f} €/kWh"
                )
                _LOGGER.debug(
                    "Found %d-hour charging window: %.1f hours of low prices (%s) ahead",
                    min_duration_hours,
                    low_price_duration.total_seconds() / 3600,
                    threshold_note,
                )
                return True

        _LOGGER.debug(
            "Charging window too short: only %.1f hours of low prices from now (need %d)",
            low_price_duration.total_seconds() / 3600,
            min_duration_hours,
        )
        return False
