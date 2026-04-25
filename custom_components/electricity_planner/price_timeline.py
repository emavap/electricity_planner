"""Price timeline construction and export-slot selection.

Extracted from ``coordinator.py`` as a standalone collaborator. Responsible
for turning raw Nord Pool interval dicts into chronologically-ordered
``PriceInterval`` lists, applying the purchase or feed-in price adjustments,
and selecting the highest-value export slots for battery-dump planning.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

from .const import (
    CONF_FEEDIN_ADJUSTMENT_MULTIPLIER,
    CONF_FEEDIN_ADJUSTMENT_OFFSET,
    CONF_PRICE_ADJUSTMENT_MULTIPLIER,
    CONF_PRICE_ADJUSTMENT_OFFSET,
    DEFAULT_FEEDIN_ADJUSTMENT_MULTIPLIER,
    DEFAULT_FEEDIN_ADJUSTMENT_OFFSET,
    DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER,
    DEFAULT_PRICE_ADJUSTMENT_OFFSET,
    PRICE_INTERVAL_LOOKBACK_HOURS,
    PRICE_INTERVAL_MINUTES,
    PRICE_VALUE_MAX_EUR_MWH,
    PRICE_VALUE_MIN_EUR_MWH,
)
from .helpers import (
    PriceInterval,
    apply_price_adjustment,
    extract_price_from_interval,
)

if TYPE_CHECKING:
    from .coordinator import ElectricityPlannerCoordinator

_LOGGER = logging.getLogger(__name__)


class PriceTimelineBuilder:
    """Build price timelines and select export slots."""

    def __init__(self, coordinator: "ElectricityPlannerCoordinator") -> None:
        self._coordinator = coordinator

    def parse_intervals(
        self,
        prices_today: dict[str, Any] | None,
        prices_tomorrow: dict[str, Any] | None,
        now: datetime,
        price_fn: Callable[[dict[str, Any], float, datetime], float | None],
    ) -> list[PriceInterval]:
        """Build a chronological price timeline using a pluggable price function.

        The *price_fn* callback receives ``(interval, raw_price_kwh, start_utc)``
        and must return the final €/kWh price to record, or ``None`` to skip the
        interval.
        """
        future_intervals: list[tuple[datetime, datetime | None, float]] = []

        def process_intervals(intervals: list[dict[str, Any]]) -> None:
            for interval in intervals:
                try:
                    start_time_str = interval.get("start")
                    if not start_time_str:
                        continue

                    start_time = dt_util.parse_datetime(start_time_str)
                    if start_time is None:
                        continue
                    start_time_utc = dt_util.as_utc(start_time)

                    end_time: datetime | None = None
                    end_time_str = interval.get("end")
                    if end_time_str:
                        try:
                            parsed_end = dt_util.parse_datetime(end_time_str)
                            if parsed_end is not None:
                                end_time_utc = dt_util.as_utc(parsed_end)
                                if end_time_utc > start_time_utc:
                                    end_time = end_time_utc
                        except Exception as exc:
                            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                                raise
                            end_time = None

                    if end_time is not None:
                        if end_time <= now:
                            continue
                    else:
                        if start_time_utc < now - timedelta(hours=PRICE_INTERVAL_LOOKBACK_HOURS):
                            continue

                    price_value = extract_price_from_interval(interval)
                    if price_value is None:
                        continue

                    if not PRICE_VALUE_MIN_EUR_MWH <= price_value <= PRICE_VALUE_MAX_EUR_MWH:
                        _LOGGER.warning(
                            "Suspicious price value %.2f €/MWh outside expected range [%d, %d], skipping interval",
                            price_value, PRICE_VALUE_MIN_EUR_MWH, PRICE_VALUE_MAX_EUR_MWH
                        )
                        continue

                    raw_price_kwh = price_value / 1000
                    final_price = price_fn(interval, raw_price_kwh, start_time_utc)
                    if final_price is None:
                        continue

                    future_intervals.append((start_time_utc, end_time, final_price))

                except (ValueError, TypeError, KeyError, AttributeError) as err:
                    _LOGGER.debug(
                        "Expected error processing interval for price timeline: %s", err
                    )
                    continue
                except Exception as err:
                    if isinstance(err, (KeyboardInterrupt, SystemExit)):
                        raise
                    _LOGGER.warning(
                        "Unexpected error processing interval for price timeline: %s",
                        err, exc_info=True
                    )
                    continue

        if prices_today:
            area_code = next(iter(prices_today.keys()), None)
            if area_code and isinstance(prices_today[area_code], list):
                process_intervals(prices_today[area_code])

        if prices_tomorrow:
            area_code = next(iter(prices_tomorrow.keys()), None)
            if area_code and isinstance(prices_tomorrow[area_code], list):
                process_intervals(prices_tomorrow[area_code])

        future_intervals.sort(key=lambda item: item[0])

        if not future_intervals:
            return []

        estimated_resolution: timedelta | None = None
        for idx in range(len(future_intervals) - 1):
            current_start = future_intervals[idx][0]
            next_start = future_intervals[idx + 1][0]
            delta = next_start - current_start
            if delta <= timedelta(0):
                continue
            if estimated_resolution is None or delta < estimated_resolution:
                estimated_resolution = delta

        if estimated_resolution is None or estimated_resolution <= timedelta(0):
            estimated_resolution = timedelta(minutes=PRICE_INTERVAL_MINUTES)

        timeline: list[PriceInterval] = []
        for idx, (start_time, end_time, price) in enumerate(future_intervals):
            if end_time and end_time > start_time:
                interval_end = end_time
            elif idx + 1 < len(future_intervals):
                next_start = future_intervals[idx + 1][0]
                if next_start > start_time:
                    interval_end = next_start
                else:
                    interval_end = start_time + estimated_resolution
            else:
                interval_end = start_time + estimated_resolution

            if interval_end <= start_time:
                continue

            timeline.append(PriceInterval(start_time, interval_end, price))

        return timeline

    def build_purchase(
        self,
        prices_today: dict[str, Any] | None,
        prices_tomorrow: dict[str, Any] | None,
        transport_lookup: list[dict[str, Any]] | None,
        current_transport_cost: float | None,
        now: datetime,
    ) -> list[PriceInterval]:
        """Build a chronological price timeline with fully resolved intervals."""
        coordinator = self._coordinator
        multiplier = coordinator.config.get(
            CONF_PRICE_ADJUSTMENT_MULTIPLIER, DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER
        )
        offset = coordinator.config.get(
            CONF_PRICE_ADJUSTMENT_OFFSET, DEFAULT_PRICE_ADJUSTMENT_OFFSET
        )

        def _purchase_price(
            interval: dict[str, Any], raw_price_kwh: float, start_utc: datetime,
        ) -> float | None:
            adjusted_price = (raw_price_kwh * multiplier) + offset
            transport_cost = coordinator._resolve_transport_cost(
                transport_lookup, start_utc, reference_now=now
            )
            if transport_cost is None:
                transport_cost = (
                    current_transport_cost
                    if current_transport_cost is not None
                    else 0.0
                )
            return adjusted_price + transport_cost

        return self.parse_intervals(
            prices_today, prices_tomorrow, now, _purchase_price,
        )

    def build_feedin(
        self,
        prices_today: dict[str, Any] | None,
        prices_tomorrow: dict[str, Any] | None,
        now: datetime,
    ) -> list[PriceInterval]:
        """Build a chronological price timeline for feed-in planning."""
        coordinator = self._coordinator
        multiplier = coordinator.config.get(
            CONF_FEEDIN_ADJUSTMENT_MULTIPLIER,
            DEFAULT_FEEDIN_ADJUSTMENT_MULTIPLIER,
        )
        offset = coordinator.config.get(
            CONF_FEEDIN_ADJUSTMENT_OFFSET,
            DEFAULT_FEEDIN_ADJUSTMENT_OFFSET,
        )

        def _feedin_price(
            interval: dict[str, Any], raw_price_kwh: float, start_utc: datetime,
        ) -> float | None:
            final_price = apply_price_adjustment(raw_price_kwh, multiplier, offset)
            return final_price if final_price is not None else raw_price_kwh

        return self.parse_intervals(
            prices_today, prices_tomorrow, now, _feedin_price,
        )

    def select_export_slots(
        self,
        timeline: list[PriceInterval],
        now: datetime,
        required_duration: timedelta,
        minimum_price: float,
        latest_end: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Select the highest-value eligible export slots and derive an arbitrage threshold."""
        if required_duration <= timedelta(0):
            return None

        eligible_slots: list[dict[str, Any]] = []
        current_slot_price: float | None = None

        for start, end, price in timeline:
            if latest_end is not None and start >= latest_end:
                continue
            if latest_end is not None and end > latest_end:
                end = latest_end
            if end <= now or price < minimum_price:
                continue

            segment_start = max(start, now)
            if end <= segment_start:
                continue
            if start <= now < end:
                current_slot_price = price
            eligible_slots.append(
                {
                    "start": segment_start,
                    "end": end,
                    "price": price,
                    "duration": end - segment_start,
                }
            )

        if not eligible_slots:
            return None

        selected_slots: list[dict[str, Any]] = []
        selected_duration = timedelta(0)

        for slot in sorted(
            eligible_slots,
            key=lambda item: (-item["price"], item["start"]),
        ):
            if selected_duration >= required_duration:
                break

            selected_slots.append(slot)
            selected_duration += slot["duration"]

        if not selected_slots:
            return None

        selected_slots.sort(key=lambda item: item["start"])
        selected_prices = [float(slot["price"]) for slot in selected_slots]
        export_price_threshold = min(selected_prices) if selected_prices else None
        total_hours = selected_duration.total_seconds() / 3600
        return {
            "selected_slots": selected_slots,
            "selected_slots_count": len(selected_slots),
            "export_price_threshold": export_price_threshold,
            "current_slot_price": current_slot_price,
            "covers_full_export": selected_duration >= required_duration,
            "selected_duration_hours": round(total_hours, 3),
        }

    def select_buy_slots(
        self,
        timeline: list[PriceInterval],
        now: datetime,
        required_duration: timedelta,
        maximum_price: float,
        latest_end: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Select the cheapest eligible buy slots and derive a buy-price threshold.

        Symmetric counterpart to :meth:`select_export_slots`. Used by the
        Negative Arbitrage Buy planner to plan grid-charging during
        ``price <= maximum_price`` (typically negative-price) windows.
        """
        if required_duration <= timedelta(0):
            return None

        eligible_slots: list[dict[str, Any]] = []
        current_slot_price: float | None = None

        for start, end, price in timeline:
            if latest_end is not None and start >= latest_end:
                continue
            if latest_end is not None and end > latest_end:
                end = latest_end
            if end <= now or price > maximum_price:
                continue

            segment_start = max(start, now)
            if end <= segment_start:
                continue
            if start <= now < end:
                current_slot_price = price
            eligible_slots.append(
                {
                    "start": segment_start,
                    "end": end,
                    "price": price,
                    "duration": end - segment_start,
                }
            )

        if not eligible_slots:
            return None

        selected_slots: list[dict[str, Any]] = []
        selected_duration = timedelta(0)

        for slot in sorted(
            eligible_slots,
            key=lambda item: (item["price"], item["start"]),
        ):
            if selected_duration >= required_duration:
                break

            selected_slots.append(slot)
            selected_duration += slot["duration"]

        if not selected_slots:
            return None

        selected_slots.sort(key=lambda item: item["start"])
        selected_prices = [float(slot["price"]) for slot in selected_slots]
        buy_price_threshold = max(selected_prices) if selected_prices else None
        total_hours = selected_duration.total_seconds() / 3600
        return {
            "selected_slots": selected_slots,
            "selected_slots_count": len(selected_slots),
            "buy_price_threshold": buy_price_threshold,
            "current_slot_price": current_slot_price,
            "covers_full_charge": selected_duration >= required_duration,
            "selected_duration_hours": round(total_hours, 3),
        }

