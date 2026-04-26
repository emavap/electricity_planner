"""Rolling-average threshold calculator with hysteresis.

Extracted from ``coordinator.py`` as a standalone collaborator. Owns the
hysteresis state (``valid_count`` / ``enabled``) that decides when the
calculated rolling average becomes active for charging decisions.

The calculator still depends on the coordinator for runtime access to
config values and the transport-cost resolver (which reads coordinator
state). That dependency is passed in as a reference.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

from .const import (
    AVERAGE_THRESHOLD_DEFAULT_INTERVAL_SECONDS,
    AVERAGE_THRESHOLD_HYSTERESIS_COUNT,
    CONF_PRICE_ADJUSTMENT_MULTIPLIER,
    CONF_PRICE_ADJUSTMENT_OFFSET,
    DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER,
    DEFAULT_PRICE_ADJUSTMENT_OFFSET,
)
from .helpers import extract_price_from_interval, parse_datetime_cached

if TYPE_CHECKING:
    from .coordinator import ElectricityPlannerCoordinator

_LOGGER = logging.getLogger(__name__)

# Minimum hours required for a stable average threshold
_MIN_HOURS = 24


class ThresholdCalculator:
    """Calculate a 24h rolling-average price threshold with hysteresis."""

    def __init__(self, coordinator: "ElectricityPlannerCoordinator") -> None:
        self._coordinator = coordinator
        self.valid_count: int = 0
        self.enabled: bool = False

    def calculate(
        self,
        prices_today: dict[str, Any] | None,
        prices_tomorrow: dict[str, Any] | None,
        transport_lookup: list[dict[str, Any]] | None,
    ) -> float | None:
        """Calculate average threshold from a minimum 24-hour rolling window.

        Uses future prices when available, but backfills with recent past prices
        to ensure at least 24 hours of data for a stable threshold.
        """
        if not prices_today and not prices_tomorrow:
            return None

        now = dt_util.utcnow()
        all_intervals: list[tuple[datetime, float]] = []

        config = self._coordinator.config
        multiplier = config.get(
            CONF_PRICE_ADJUSTMENT_MULTIPLIER, DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER
        )
        offset = config.get(
            CONF_PRICE_ADJUSTMENT_OFFSET, DEFAULT_PRICE_ADJUSTMENT_OFFSET
        )

        def process_intervals(
            intervals: list[dict[str, Any]],
            include_past: bool = False,
            include_future: bool = True,
        ) -> None:
            """Process intervals and add them to ``all_intervals``."""
            for interval in intervals:
                try:
                    start_time_str = interval.get("start")
                    if not start_time_str:
                        continue

                    start_time = parse_datetime_cached(start_time_str)
                    if start_time is None:
                        continue
                    start_time_utc = dt_util.as_utc(start_time)

                    is_past = start_time_utc < now
                    if is_past and not include_past:
                        continue
                    if not is_past and not include_future:
                        continue

                    price_value = extract_price_from_interval(interval)
                    if price_value is None:
                        continue

                    price_kwh = price_value / 1000
                    adjusted_price = (price_kwh * multiplier) + offset

                    transport_cost = self._coordinator._resolve_transport_cost(
                        transport_lookup, start_time_utc, reference_now=now
                    )
                    if transport_cost is None:
                        transport_cost = 0.0

                    final_price = adjusted_price + transport_cost
                    all_intervals.append((start_time_utc, final_price))

                except (ValueError, TypeError, KeyError) as err:
                    _LOGGER.debug(
                        "Expected error processing interval for average threshold: %s", err
                    )
                    continue
                except Exception as err:
                    if isinstance(err, (KeyboardInterrupt, SystemExit)):
                        raise
                    _LOGGER.warning(
                        "Unexpected error processing interval for average threshold: %s",
                        err, exc_info=True
                    )
                    continue

        def infer_interval_resolution() -> timedelta:
            """Infer typical Nord Pool interval duration from provided data."""
            deltas: list[timedelta] = []

            def collect(intervals: list[dict[str, Any]]) -> None:
                timestamps: list[datetime] = []
                for interval in intervals:
                    start_time_str = interval.get("start")
                    if not start_time_str:
                        continue
                    start_time = parse_datetime_cached(start_time_str)
                    if start_time is None:
                        continue
                    timestamps.append(dt_util.as_utc(start_time))

                timestamps.sort()
                for idx in range(1, len(timestamps)):
                    delta = timestamps[idx] - timestamps[idx - 1]
                    if delta > timedelta(0):
                        deltas.append(delta)

            if isinstance(prices_today, dict):
                for intervals in prices_today.values():
                    if isinstance(intervals, list):
                        collect(intervals)

            if isinstance(prices_tomorrow, dict):
                for intervals in prices_tomorrow.values():
                    if isinstance(intervals, list):
                        collect(intervals)

            if deltas:
                return min(deltas)

            # Default fallback: 15-minute intervals
            return timedelta(minutes=15)

        def collect_past_intervals(max_count: int) -> list[tuple[datetime, float]]:
            """Collect recent past price intervals for backfilling."""
            past_intervals: list[tuple[datetime, float]] = []

            if not prices_today:
                return past_intervals

            area_code = next(iter(prices_today.keys()), None)
            if not area_code or not isinstance(prices_today[area_code], list):
                return past_intervals

            for interval in prices_today[area_code]:
                try:
                    start_time_str = interval.get("start")
                    if not start_time_str:
                        continue

                    start_time = parse_datetime_cached(start_time_str)
                    if start_time is None:
                        continue
                    start_time_utc = dt_util.as_utc(start_time)

                    if start_time_utc >= now:
                        continue

                    price_value = extract_price_from_interval(interval)
                    if price_value is None:
                        continue

                    price_kwh = price_value / 1000
                    adjusted_price = (price_kwh * multiplier) + offset
                    transport_cost = self._coordinator._resolve_transport_cost(
                        transport_lookup, start_time_utc, reference_now=now
                    )
                    if transport_cost is None:
                        transport_cost = 0.0
                    final_price = adjusted_price + transport_cost

                    past_intervals.append((start_time_utc, final_price))
                except Exception as exc:
                    if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                        raise
                    continue

            past_intervals.sort(key=lambda x: x[0], reverse=True)
            limited = past_intervals[:max_count]
            limited.reverse()
            return limited

        # Pass 1: Collect future intervals only
        if prices_today:
            area_code = next(iter(prices_today.keys()), None)
            if area_code and isinstance(prices_today[area_code], list):
                process_intervals(prices_today[area_code], include_past=False, include_future=True)

        if prices_tomorrow:
            area_code = next(iter(prices_tomorrow.keys()), None)
            if area_code and isinstance(prices_tomorrow[area_code], list):
                process_intervals(prices_tomorrow[area_code], include_past=False, include_future=True)

        all_intervals.sort(key=lambda x: x[0])

        future_intervals = [(t, p) for t, p in all_intervals if t >= now]

        if not future_intervals:
            _LOGGER.warning("No future price intervals available for average threshold")
            return None

        interval_duration = infer_interval_resolution()

        if interval_duration <= timedelta(0):
            interval_duration = timedelta(minutes=15)

        interval_seconds = interval_duration.total_seconds()
        if interval_seconds <= 0:
            _LOGGER.warning("Invalid interval duration detected, using 15-minute default")
            interval_seconds = AVERAGE_THRESHOLD_DEFAULT_INTERVAL_SECONDS
        intervals_needed = max(1, int(_MIN_HOURS * 3600 / interval_seconds))

        # Pass 2: backfill with past intervals if necessary
        if len(future_intervals) < intervals_needed:
            past_intervals_needed = intervals_needed - len(future_intervals)
            past_intervals = collect_past_intervals(past_intervals_needed)

            if len(past_intervals) >= past_intervals_needed:
                combined_intervals = past_intervals + future_intervals
                past_count = len(past_intervals)
                future_count = len(future_intervals)

                _LOGGER.debug(
                    "Average threshold: using %d past + %d future intervals (%.1fh total) to meet %dh minimum",
                    past_count, future_count,
                    (past_count + future_count) * interval_duration.total_seconds() / 3600,
                    _MIN_HOURS
                )
            else:
                combined_intervals = future_intervals
                past_count = 0
                future_count = len(future_intervals)
                _LOGGER.debug(
                    "Average threshold: insufficient past data (have %d intervals, need %d) – using %d future intervals only",
                    len(past_intervals),
                    past_intervals_needed,
                    future_count,
                )
        else:
            combined_intervals = future_intervals
            past_count = 0
            future_count = len(future_intervals)

            _LOGGER.debug(
                "Average threshold: using %d future intervals (%.1fh total)",
                future_count, future_count * interval_duration.total_seconds() / 3600
            )

        if combined_intervals:
            prices = [p for _, p in combined_intervals]
            average = sum(prices) / len(prices)

            has_sufficient_data = len(combined_intervals) >= intervals_needed

            if has_sufficient_data:
                if not self.enabled:
                    self.valid_count += 1

                    if self.valid_count >= AVERAGE_THRESHOLD_HYSTERESIS_COUNT:
                        self.enabled = True
                        _LOGGER.info(
                            "Average threshold enabled after %d consecutive valid calculations: %.4f €/kWh",
                            self.valid_count, average
                        )
                    else:
                        _LOGGER.debug(
                            "Average threshold calculated (%.4f €/kWh) but not yet enabled "
                            "(%d/%d consecutive valid calculations)",
                            average, self.valid_count, AVERAGE_THRESHOLD_HYSTERESIS_COUNT
                        )
                # else: already enabled, keep using

                _LOGGER.debug(
                    "Calculated average threshold: %.4f €/kWh from %d intervals (enabled: %s, count: %d)",
                    average, len(combined_intervals), self.enabled,
                    self.valid_count
                )
                return round(average, 4)

            # Insufficient data for 24h minimum
            if self.enabled:
                _LOGGER.warning(
                    "Average threshold: insufficient data for %dh minimum "
                    "(have %d intervals, need %d) - continuing with available data",
                    _MIN_HOURS, len(combined_intervals), intervals_needed
                )
                return round(average, 4)

            _LOGGER.debug(
                "Average threshold: insufficient data for %dh minimum "
                "(have %d intervals, need %d) - returning value without enabling",
                _MIN_HOURS, len(combined_intervals), intervals_needed
            )
            self.valid_count = 0
            return round(average, 4)

        # No intervals available - reset
        if self.valid_count > 0 or self.enabled:
            _LOGGER.debug("Average threshold data unavailable - resetting hysteresis state")
        self.valid_count = 0
        self.enabled = False
        return None
