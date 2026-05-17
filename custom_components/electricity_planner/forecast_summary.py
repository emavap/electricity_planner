"""Forecast summary: cheapest interval and best charging window computation.

Extracted from ``coordinator.py`` as a standalone collaborator. Operates on
the coordinator's cached price timeline state, invalidating it when the
underlying price-data hash changes or when the cache exceeds its max age.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

from .const import (
    CONF_MIN_CAR_CHARGING_DURATION,
    DEFAULT_MIN_CAR_CHARGING_DURATION,
)

if TYPE_CHECKING:
    from .coordinator import ElectricityPlannerCoordinator

_LOGGER = logging.getLogger(__name__)


class ForecastSummaryCalculator:
    """Build forecast insights (cheapest interval, best charging window)."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator) -> None:
        self._coordinator = coordinator

    def calculate(
        self,
        prices_today: dict[str, Any] | None,
        prices_tomorrow: dict[str, Any] | None,
        transport_lookup: list[dict[str, Any]] | None,
        current_transport_cost: float | None,
        minimum_average_threshold: float | None,
    ) -> dict[str, Any]:
        """Produce forecast insights (cheapest interval and best charging window)."""
        coordinator = self._coordinator
        now = dt_util.utcnow()

        stale = False

        # Check if cached timeline is too old
        if (
            coordinator._last_price_timeline_generated_at is not None
            and now - coordinator._last_price_timeline_generated_at
            > coordinator._price_timeline_max_age
        ):
            _LOGGER.debug(
                "Price timeline cache expired (age: %.1f hours), clearing",
                (now - coordinator._last_price_timeline_generated_at).total_seconds()
                / 3600,
            )
            coordinator._last_price_timeline = None
            coordinator._last_price_timeline_generated_at = None
            coordinator._last_price_timeline_data_hash = None

        # Compute hash of current price data to detect changes
        current_price_hash = coordinator._compute_price_data_hash(
            prices_today, prices_tomorrow
        )

        # Invalidate cache if price data has changed — but only when we
        # actually *have* new price data.  When both are None the caller has
        # no data at all and we should fall through to the stale-cache path
        # below instead of wiping the cache.
        if (prices_today or prices_tomorrow) and (
            coordinator._last_price_timeline is not None
            and coordinator._last_price_timeline_data_hash != current_price_hash
        ):
            _LOGGER.debug(
                "Price data changed (hash mismatch), invalidating timeline cache"
            )
            coordinator._last_price_timeline = None
            coordinator._last_price_timeline_generated_at = None
            coordinator._last_price_timeline_data_hash = None

        if not prices_today and not prices_tomorrow:
            if coordinator._last_price_timeline:
                timeline = coordinator._last_price_timeline
                stale = True
            else:
                coordinator._last_price_timeline = None
                coordinator._last_price_timeline_generated_at = None
                coordinator._last_price_timeline_data_hash = None
                return {"available": False}
        else:
            timeline = (
                coordinator._last_price_timeline
                or coordinator._build_price_timeline(
                    prices_today,
                    prices_tomorrow,
                    transport_lookup,
                    current_transport_cost,
                    now,
                )
            )
            if timeline and coordinator._last_price_timeline is None:
                coordinator._last_price_timeline = timeline
                coordinator._last_price_timeline_generated_at = now
                coordinator._last_price_timeline_data_hash = current_price_hash

        if not timeline:
            coordinator._last_price_timeline = None
            coordinator._last_price_timeline_generated_at = None
            coordinator._last_price_timeline_data_hash = None
            return {"available": False}

        # Consider only intervals that are in the future
        future_segments = [
            (start, end, price) for start, end, price in timeline if end > now
        ]
        if not future_segments:
            coordinator._last_price_timeline = None
            coordinator._last_price_timeline_generated_at = None
            coordinator._last_price_timeline_data_hash = None
            return {"available": False}

        cheapest_segment = min(future_segments, key=lambda segment: segment[2])

        min_duration_hours = coordinator.config.get(
            CONF_MIN_CAR_CHARGING_DURATION, DEFAULT_MIN_CAR_CHARGING_DURATION
        )
        required_duration = timedelta(hours=min_duration_hours)

        best_window = self._find_best_window(future_segments, now, required_duration)

        def _iso_local(dt_obj: datetime) -> str:
            """Return ISO string in Home Assistant's local timezone."""
            localized = dt_util.as_local(dt_obj)
            return localized.isoformat()

        summary: dict[str, Any] = {
            "available": True,
            "cheapest_interval_start": _iso_local(cheapest_segment[0]),
            "cheapest_interval_end": _iso_local(cheapest_segment[1]),
            "cheapest_interval_price": round(cheapest_segment[2], 4),
            "average_threshold": minimum_average_threshold,
            "evaluated_at": _iso_local(now),
        }

        if stale:
            summary["stale"] = True
        if coordinator._last_price_timeline_generated_at:
            summary["timeline_generated_at"] = _iso_local(
                coordinator._last_price_timeline_generated_at
            )

        if best_window:
            summary.update(
                {
                    "best_window_hours": min_duration_hours,
                    "best_window_start": _iso_local(best_window["start"]),
                    "best_window_end": _iso_local(best_window["end"]),
                    "best_window_average_price": round(best_window["average_price"], 4),
                }
            )

        return summary

    @staticmethod
    def _find_best_window(
        future_segments: list[tuple[datetime, datetime, float]],
        now: datetime,
        required_duration: timedelta,
    ) -> dict[str, Any] | None:
        """Scan future segments for the cheapest contiguous window of required duration."""
        best_window: dict[str, Any] | None = None
        for idx in range(len(future_segments)):
            window_start = max(future_segments[idx][0], now)
            window_end_target = window_start + required_duration
            window_duration = timedelta(0)
            price_sum = 0.0
            current_time = window_start

            for segment_idx in range(idx, len(future_segments)):
                segment_start, segment_end, segment_price = future_segments[segment_idx]
                if segment_end <= current_time:
                    continue

                effective_start = max(segment_start, current_time)
                effective_end = min(segment_end, window_end_target)

                if effective_end <= effective_start:
                    continue

                duration = effective_end - effective_start
                hours = duration.total_seconds() / 3600
                price_sum += segment_price * hours
                window_duration += duration
                current_time = effective_end

                if current_time >= window_end_target:
                    break

            if window_duration >= required_duration and window_duration > timedelta(0):
                hours_total = window_duration.total_seconds() / 3600
                if hours_total <= 0:
                    continue
                average_price = price_sum / hours_total
                if best_window is None or average_price < best_window["average_price"]:
                    best_window = {
                        "start": window_start,
                        "end": current_time,
                        "average_price": average_price,
                    }
        return best_window
