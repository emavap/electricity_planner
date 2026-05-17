"""Helper functions and utilities for Electricity Planner."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from functools import lru_cache
from typing import Any, NamedTuple

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.util import dt as dt_util

try:
    from pytz import AmbiguousTimeError, NonExistentTimeError
except ImportError:  # pragma: no cover - Home Assistant test env provides pytz
    AmbiguousTimeError = NonExistentTimeError = ValueError

from .const import (
    MONTH_PEAK_TRANSITION_LEAD_MINUTES,
    POWER_ALLOCATION_PRECISION,
    POWER_ALLOCATION_TOLERANCE,
    PRICE_POSITION_CACHE_SIZE,
    TARIFF_DAY_END_HOUR,
    TARIFF_DAY_START_HOUR,
)

_LOGGER = logging.getLogger(__name__)
_INTEGER_RE = re.compile(r"^[+-]?\d+$")


@lru_cache(maxsize=1024)
def _parse_datetime_cached(value: str) -> datetime | None:
    """LRU-backed delegate for ``dt_util.parse_datetime`` keyed on the raw string."""
    return dt_util.parse_datetime(value)


def parse_datetime_cached(value: Any) -> datetime | None:
    """Cached wrapper around ``dt_util.parse_datetime`` for ISO-8601 strings.

    The same ~192 Nord Pool interval ``start`` / ``end`` strings are parsed
    multiple times per update cycle (across price-timeline construction,
    threshold-calculator passes, price-analysis overrides, and the dashboard
    sensor rendering). Caching by string lets the second-and-later visit
    return the previously parsed ``datetime`` without re-running the
    relatively expensive ISO-8601 parser.

    Non-string inputs and ``None`` defer to the underlying helper so callers
    keep their original validation semantics.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return dt_util.parse_datetime(value)
    return _parse_datetime_cached(value)


def extract_price_from_interval(interval: dict[str, Any]) -> float | None:
    """Extract price value from a Nord Pool interval dict.

    Tries multiple keys used by different Nord Pool sensor versions:
    'value', 'value_exc_vat', and 'price'.

    Args:
        interval: Dictionary representing a single Nord Pool price interval.

    Returns:
        The price as a float, or None if no valid price key is found.
    """
    for key in ("value", "value_exc_vat", "price"):
        value = interval.get(key)
        if isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, str):
            try:
                return float(value)
            except (ValueError, TypeError):
                continue
    return None


class PriceInterval(NamedTuple):
    """A single price interval with start time, end time, and price.

    Implemented as a NamedTuple so it can be used as a drop-in replacement
    for the ``(start, end, price)`` tuples used throughout the codebase while
    also providing named attribute access for clarity.
    """

    start: datetime
    end: datetime
    price: float


def coerce_integral_range(
    value: Any,
    *,
    min_value: int,
    max_value: int,
) -> int | None:
    """Return an integral value within range, or ``None`` if invalid."""
    parsed: int

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, float):
        if not value.is_integer():
            return None
        parsed = int(value)
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped or _INTEGER_RE.match(stripped) is None:
            return None
        parsed = int(stripped)
    else:
        return None

    if not min_value <= parsed <= max_value:
        return None
    return parsed


def _coerce_local_wall_clock(
    naive_local: datetime,
    *,
    prefer_earliest_ambiguous: bool = True,
) -> datetime:
    """Attach the Home Assistant timezone to a wall-clock datetime deterministically."""
    timezone = dt_util.DEFAULT_TIME_ZONE
    if hasattr(timezone, "localize"):
        try:
            return timezone.localize(naive_local, is_dst=None)
        except NonExistentTimeError:
            for minutes in range(1, 181):
                shifted_local = naive_local + timedelta(minutes=minutes)
                try:
                    return timezone.localize(shifted_local, is_dst=None)
                except NonExistentTimeError:
                    continue
                except AmbiguousTimeError:
                    return timezone.localize(
                        shifted_local,
                        is_dst=prefer_earliest_ambiguous,
                    )
        except AmbiguousTimeError:
            return timezone.localize(
                naive_local,
                is_dst=prefer_earliest_ambiguous,
            )

    candidates: list[datetime] = []
    for fold in (0, 1):
        candidate = naive_local.replace(tzinfo=timezone, fold=fold)
        round_tripped = (
            candidate.astimezone(dt_timezone.utc)
            .astimezone(timezone)
            .replace(tzinfo=None)
        )
        if round_tripped == naive_local:
            candidates.append(candidate)

    if candidates:
        return (
            min(
                candidates,
                key=lambda candidate: candidate.astimezone(dt_timezone.utc),
            )
            if prefer_earliest_ambiguous
            else max(
                candidates,
                key=lambda candidate: candidate.astimezone(dt_timezone.utc),
            )
        )

    for minutes in range(1, 181):
        shifted_local = naive_local + timedelta(minutes=minutes)
        for fold in (0, 1):
            candidate = shifted_local.replace(tzinfo=timezone, fold=fold)
            round_tripped = (
                candidate.astimezone(dt_timezone.utc)
                .astimezone(timezone)
                .replace(tzinfo=None)
            )
            if round_tripped == shifted_local:
                return candidate

    raise ValueError(f"Unable to localize wall-clock time {naive_local.isoformat()}")


def _coerce_local_datetime(naive_local: datetime) -> datetime:
    """Attach Home Assistant's local timezone to a naive wall-clock datetime."""
    return _coerce_local_wall_clock(naive_local, prefer_earliest_ambiguous=True)


def resolve_local_deadline(
    local_date: date,
    hour: int,
    *,
    minute: int = 0,
) -> datetime:
    """Return the first valid local instant at or after a wall-clock deadline."""
    naive_local = datetime(
        local_date.year,
        local_date.month,
        local_date.day,
        hour,
        minute,
    )
    return _coerce_local_wall_clock(naive_local, prefer_earliest_ambiguous=True)


def is_in_month_peak_transition_window(
    now: datetime | None = None,
    *,
    lead_minutes: int = MONTH_PEAK_TRANSITION_LEAD_MINUTES,
) -> bool:
    """Return whether month-boundary protection should ignore the current peak sensor.

    The protection starts shortly before local month end and remains active for the
    same grace period after local month start. This prevents a stale monthly peak
    sensor from briefly reapplying the previous month's higher peak right after
    midnight before the source entity resets.
    """
    reference_now = now or dt_util.utcnow()
    local_now = dt_util.as_local(reference_now)
    current_month_start = resolve_local_deadline(
        date(local_now.year, local_now.month, 1), 0
    )

    if local_now < current_month_start + timedelta(minutes=lead_minutes):
        return True

    if local_now.month == 12:
        next_month_date = date(local_now.year + 1, 1, 1)
    else:
        next_month_date = date(local_now.year, local_now.month + 1, 1)

    next_month_start = resolve_local_deadline(next_month_date, 0)
    return local_now >= next_month_start - timedelta(minutes=lead_minutes)


def _same_local_time_last_week(target_time_utc: datetime) -> datetime:
    """Return the same local wall-clock time one calendar week earlier."""
    target_local = dt_util.as_local(target_time_utc)
    week_ago_naive = target_local.replace(tzinfo=None) - timedelta(days=7)
    return _coerce_local_datetime(week_ago_naive)


def _entry_local_datetime(entry: dict[str, Any]) -> datetime | None:
    """Return the cached local datetime for a transport-lookup entry.

    Production lookups built by ``TransportCostResolver.get_lookup`` already
    embed a parsed ``_local`` field, which avoids a ~134 k ``parse_datetime``
    calls per update cycle (192 intervals \u00d7 ~672 history entries). Tests and
    legacy callers pass raw ``{"start": iso_str, "cost": x}`` dicts \u2014 fall
    back to parsing on demand for those.
    """
    cached = entry.get("_local")
    if isinstance(cached, datetime):
        return cached

    entry_start_str = entry.get("start")
    if entry_start_str is None:
        return None
    parsed = parse_datetime_cached(entry_start_str)
    if parsed is None:
        return None
    return dt_util.as_local(dt_util.as_utc(parsed))


def resolve_transport_cost_from_lookup(
    transport_lookup: list[dict[str, Any]] | None,
    start_time_utc: datetime,
    reference_now: datetime | None = None,
) -> float | None:
    """Resolve transport cost for a timestamp using local-time weekly matching."""
    if not transport_lookup:
        return None

    if reference_now is None:
        reference_now = dt_util.utcnow()

    target_local = dt_util.as_local(start_time_utc)

    if start_time_utc > reference_now:
        week_ago_local = _same_local_time_last_week(start_time_utc)
        cost_from_pattern: float | None = None
        for entry in transport_lookup:
            entry_cost = entry.get("cost")
            if entry_cost is None:
                continue
            if entry.get("start") is None:
                cost_from_pattern = float(entry_cost)
                continue
            entry_local = _entry_local_datetime(entry)
            if entry_local is None:
                continue
            if entry_local <= week_ago_local:
                cost_from_pattern = float(entry_cost)
            else:
                break

        if cost_from_pattern is not None:
            return cost_from_pattern

    cost: float | None = None
    for entry in transport_lookup:
        entry_cost = entry.get("cost")
        if entry_cost is None:
            continue
        if entry.get("start") is None:
            cost = float(entry_cost)
            continue
        entry_local = _entry_local_datetime(entry)
        if entry_local is None:
            continue
        if entry_local <= target_local:
            cost = float(entry_cost)
        else:
            break

    return cost


def is_day_tariff(timestamp_utc: datetime, p1_tariff_code: str | None = None) -> bool:
    """Determine if a timestamp falls in the Belgian day tariff period.

    Uses the P1 meter tariff code if available (real-time), otherwise
    applies the standard Fluvius day/night schedule:
      Day:   Mon-Fri 07:00 - 22:00 local time
      Night: Mon-Fri 22:00 - 07:00 local time + all day Sat/Sun

    Args:
        timestamp_utc: The UTC timestamp to evaluate.
        p1_tariff_code: Current P1 meter tariff code ("1" = day, "2" = night).
            Only used when timestamp is within the current 15-minute window.

    Returns:
        True if the timestamp falls in the day tariff period.
    """
    if p1_tariff_code is not None:
        # P1 tariff codes: "1" = day/peak, "2" = night/off-peak
        return str(p1_tariff_code).strip() == "1"

    # Fall back to standard Belgian schedule for future/past timestamps
    local_time = dt_util.as_local(timestamp_utc)
    weekday = local_time.weekday()  # 0=Mon, 6=Sun

    # Weekend = always night tariff
    if weekday >= 5:
        return False

    # Weekday: day tariff from 07:00 to 22:00
    return TARIFF_DAY_START_HOUR <= local_time.hour < TARIFF_DAY_END_HOUR


def calculate_transport_cost_from_components(
    is_day: bool,
    transport_day: float,
    transport_night: float,
    accijns: float,
    bijdrage: float,
    gsc: float,
    wkk: float,
) -> float:
    """Calculate total transport cost from individual components.

    Args:
        is_day: True if day tariff applies, False for night/weekend.
        transport_day: Fluvius day network tariff (€/kWh).
        transport_night: Fluvius night/weekend network tariff (€/kWh).
        accijns: Bijzondere accijns op Energie (€/kWh).
        bijdrage: Bijdrage op de Energie (€/kWh).
        gsc: Groene stroom certificaten (€/kWh).
        wkk: Warmte-krachtkoppeling (€/kWh).

    Returns:
        Total transport + taxes + certificates cost in €/kWh.
    """
    network_rate = transport_day if is_day else transport_night
    return network_rate + accijns + bijdrage + gsc + wkk


class DataValidator:
    """Validate and sanitize data."""

    @staticmethod
    def validate_power_value(
        power: float,
        min_value: float = 0,
        max_value: float | None = None,
        name: str = "power",
    ) -> float:
        """Validate and clamp power values to safe ranges.

        Args:
            power: Power value in Watts
            min_value: Minimum allowed value (default: 0)
            max_value: Maximum allowed value (default: None = no limit)
            name: Name for logging purposes

        Returns:
            Clamped power value within [min_value, max_value]
        """
        if power < min_value:
            _LOGGER.warning(
                "%s value %sW below minimum, clamping to %sW", name, power, min_value
            )
            return min_value
        if max_value is not None and power > max_value:
            _LOGGER.warning(
                "%s value %sW above maximum, clamping to %sW", name, power, max_value
            )
            return max_value
        return power

    @staticmethod
    def validate_battery_data(battery_soc_data: list[dict]) -> tuple[bool, str]:
        """Validate battery data integrity."""
        if not battery_soc_data:
            return False, "No battery entities configured"

        valid_count = sum(
            1
            for b in battery_soc_data
            if b.get("soc") is not None and 0 <= b["soc"] <= 100
        )

        if valid_count == 0:
            return False, "All battery sensors returning invalid data"

        if valid_count < len(battery_soc_data) / 2:
            _LOGGER.warning("More than 50% of battery sensors unavailable")

        return True, f"{valid_count}/{len(battery_soc_data)} sensors valid"

    @staticmethod
    def sanitize_config_value(
        value: Any, min_val: float, max_val: float, default: float, name: str = "config"
    ) -> float:
        """Sanitize configuration values to prevent issues."""
        try:
            val = float(value)
            if not min_val <= val <= max_val:
                _LOGGER.warning(
                    "%s value %s out of range [%s, %s], using default %s",
                    name,
                    val,
                    min_val,
                    max_val,
                    default,
                )
                return default
            return val
        except (TypeError, ValueError):
            _LOGGER.error("%s value %s invalid, using default %s", name, value, default)
            return default

    @staticmethod
    def is_valid_state(state: Any) -> bool:
        """Check if a state value is valid."""
        return state not in (None, STATE_UNAVAILABLE, STATE_UNKNOWN, "")


def apply_price_adjustment(
    price: float | None, multiplier: float = 1.0, offset: float = 0.0
) -> float | None:
    """Apply a simple affine transformation to a price value.

    Args:
        price: Price in €/kWh (or None)
        multiplier: Multiplicative factor (default: 1.0)
        offset: Additive offset in €/kWh (default: 0.0)

    Returns:
        Adjusted price = (price * multiplier) + offset, or None if input is None
    """
    if price is None:
        return None
    try:
        return (float(price) * float(multiplier)) + float(offset)
    except (TypeError, ValueError):
        _LOGGER.error(
            "Invalid price adjustment: price=%s, multiplier=%s, offset=%s",
            price,
            multiplier,
            offset,
        )
        return None


class PriceCalculator:
    """Price-related calculations."""

    @staticmethod
    @lru_cache(maxsize=PRICE_POSITION_CACHE_SIZE)
    def calculate_price_position(
        current: float, highest: float, lowest: float
    ) -> float:
        """Calculate price position relative to daily range (cached).

        Args:
            current: Current price value
            highest: Highest price in range
            lowest: Lowest price in range

        Returns:
            Float between 0.0 (at lowest) and 1.0 (at highest), or 0.5 if no valid range.
        """
        # Handle edge cases with zero or negative prices
        if highest == lowest:
            return 0.5  # Neutral if no valid range

        if highest > lowest:
            return (current - lowest) / (highest - lowest)

        # Inverted range (shouldn't happen, but handle gracefully)
        _LOGGER.warning(
            "Invalid price range: highest=%.4f < lowest=%.4f, returning neutral position",
            highest,
            lowest,
        )
        return 0.5

    @staticmethod
    def is_significant_price_drop(
        current_price: float, next_price: float | None, threshold: float = 0.15
    ) -> bool:
        """Check if there's a significant price drop coming."""
        if next_price is None or current_price <= 0:
            return False
        return (current_price - next_price) / current_price > threshold


class TimeContext:
    """Time-based context utilities."""

    @staticmethod
    def get_current_context() -> dict[str, Any]:
        """Get time-of-day context for charging decisions."""
        now = dt_util.now()
        return {
            "current_hour": now.hour,
            "timestamp": now.isoformat(),
        }


class PowerAllocationValidator:
    """Validate power allocation logic."""

    @staticmethod
    def validate_allocation(
        allocation: dict[str, Any],
        available_solar: float,
        max_battery_power: float,
        max_car_power: float,
    ) -> tuple[bool, str | None]:
        """Validate power allocation doesn't exceed limits."""
        solar_for_batteries = allocation.get("solar_for_batteries", 0)
        solar_for_car = allocation.get("solar_for_car", 0)
        car_current_usage = allocation.get("car_current_solar_usage", 0)
        total_allocated = allocation.get("total_allocated", 0)

        # Check individual limits
        if solar_for_batteries > max_battery_power:
            return (
                False,
                f"Battery allocation {solar_for_batteries}W exceeds limit {max_battery_power}W",
            )

        if solar_for_car > max_car_power:
            return (
                False,
                f"Car allocation {solar_for_car}W exceeds limit {max_car_power}W",
            )

        # Check total doesn't exceed available (with configurable tolerance)
        calculated_total = solar_for_batteries + solar_for_car + car_current_usage
        if calculated_total > available_solar * POWER_ALLOCATION_TOLERANCE:
            return (
                False,
                f"Total allocation {calculated_total}W exceeds available {available_solar}W",
            )

        # Check internal consistency (with configurable precision)
        if abs(calculated_total - total_allocated) > POWER_ALLOCATION_PRECISION:
            return (
                False,
                f"Allocation mismatch: sum={calculated_total}W, total={total_allocated}W",
            )

        return True, None


def format_reason(
    action: str, primary_reason: str, details: dict[str, Any] | None = None
) -> str:
    """Format a decision reason with optional details."""
    reason = f"{action}: {primary_reason}"

    if details:
        detail_parts = []
        for key, value in details.items():
            if isinstance(value, float):
                if "price" in key.lower() or "€" in str(value):
                    detail_parts.append(f"{key}={value:.3f}€/kWh")
                elif "soc" in key.lower() or "%" in str(value):
                    detail_parts.append(f"{key}={value:.0f}%")
                else:
                    detail_parts.append(f"{key}={value:.0f}W")
            else:
                detail_parts.append(f"{key}={value}")

        if detail_parts:
            reason += f" ({', '.join(detail_parts)})"

    return reason
