"""Data coordinator for Electricity Planner."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    CONF_PHASE_MODE,
    PHASE_MODE_SINGLE,
    PHASE_MODE_THREE,
    CONF_PHASES,
    PHASE_IDS,
    DEFAULT_PHASE_NAMES,
    CONF_PHASE_NAME,
    CONF_PHASE_SOLAR_ENTITY,
    CONF_PHASE_CONSUMPTION_ENTITY,
    CONF_PHASE_CAR_ENTITY,
    CONF_PHASE_BATTERY_POWER_ENTITY,
    CONF_NORDPOOL_CONFIG_ENTRY,
    CONF_CURRENT_PRICE_ENTITY,
    CONF_HIGHEST_PRICE_ENTITY,
    CONF_LOWEST_PRICE_ENTITY,
    CONF_NEXT_PRICE_ENTITY,
    CONF_BATTERY_SOC_ENTITIES,
    CONF_BATTERY_CAPACITIES,
    CONF_BATTERY_PHASE_ASSIGNMENTS,
    CONF_SOLAR_PRODUCTION_ENTITY,
    CONF_HOUSE_CONSUMPTION_ENTITY,
    CONF_CAR_CHARGING_POWER_ENTITY,
    CONF_MONTHLY_GRID_PEAK_ENTITY,
    CONF_TRANSPORT_COST_ENTITY,
    CONF_GRID_POWER_ENTITY,
    CONF_BASE_GRID_SETPOINT,
    CONF_MIN_SOC_THRESHOLD,
    CONF_MAX_SOC_THRESHOLD,
    CONF_PRICE_THRESHOLD,
    CONF_PRICE_ADJUSTMENT_MULTIPLIER,
    CONF_PRICE_ADJUSTMENT_OFFSET,
    CONF_USE_AVERAGE_THRESHOLD,
    CONF_MIN_CAR_CHARGING_DURATION,
    CONF_MIN_CAR_CHARGING_THRESHOLD,
    CONF_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
    DEFAULT_MIN_SOC,
    DEFAULT_MAX_SOC,
    DEFAULT_PRICE_THRESHOLD,
    DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER,
    DEFAULT_PRICE_ADJUSTMENT_OFFSET,
    DEFAULT_USE_AVERAGE_THRESHOLD,
    DEFAULT_MIN_CAR_CHARGING_DURATION,
    DEFAULT_MIN_CAR_CHARGING_THRESHOLD,
    DEFAULT_BASE_GRID_SETPOINT,
    DEFAULT_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
    NORDPOOL_CACHE_MAX_SIZE,
    NORDPOOL_CACHE_TTL_MINUTES,
    PRICE_TIMELINE_MAX_AGE_HOURS,
    PRICE_INTERVAL_GAP_TOLERANCE_SECONDS,
    PEAK_THRESHOLD_MULTIPLIER,
    BATTERY_CAPACITY_FALLBACK_WEIGHT,
    AVERAGE_THRESHOLD_HYSTERESIS_COUNT,
    AVERAGE_THRESHOLD_DEFAULT_INTERVAL_SECONDS,
    MIN_UPDATE_INTERVAL_SECONDS,
    PRICE_INTERVAL_LOOKBACK_HOURS,
    PEAK_MONITORING_DURATION_MINUTES,
    PEAK_LIMIT_DURATION_MINUTES,
    PRICE_INTERVAL_MINUTES,
    PRICE_VALUE_MIN_EUR_MWH,
    PRICE_VALUE_MAX_EUR_MWH,
    BATTERY_SOC_DECIMAL_THRESHOLD,
)
from .decision_engine import ChargingDecisionEngine

_LOGGER = logging.getLogger(__name__)


class ElectricityPlannerCoordinator(DataUpdateCoordinator):
    """Coordinator for electricity planner data."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.hass = hass
        self.entry = entry
        merged_config: dict[str, Any] = dict(entry.data)
        if entry.options:
            merged_config.update(entry.options)
        self.config = merged_config

        self.phase_mode: str = self.config.get(CONF_PHASE_MODE, PHASE_MODE_SINGLE)
        self.phase_configs: dict[str, Any] = self.config.get(CONF_PHASES, {})
        self.battery_phase_assignments: dict[str, list[str]] = self.config.get(
            CONF_BATTERY_PHASE_ASSIGNMENTS, {}
        )

        self.decision_engine = ChargingDecisionEngine(hass, self.config)

        # Data availability tracking
        self._last_successful_update = dt_util.utcnow()
        self._data_unavailable_since = None
        self._notification_sent = False

        # Update throttling
        self._last_entity_update = None
        self._min_update_interval = timedelta(seconds=MIN_UPDATE_INTERVAL_SECONDS)
        self._update_lock = asyncio.Lock()  # Prevent race conditions in throttling

        # Nord Pool price caching (prices only update hourly)
        # Cache structure: {cache_key: (data, timestamp)}
        self._nordpool_cache: dict[str, tuple[dict[str, Any], datetime]] = {}
        self._nordpool_cache_max_size = NORDPOOL_CACHE_MAX_SIZE

        # Transport cost lookup caching (expensive recorder query)
        self._transport_cost_lookup: list[dict[str, Any]] = []
        self._transport_cost_lookup_time: datetime | None = None
        self._transport_cost_status: str = "not_configured"
        self._transport_cost_last_log: str | None = None

        # Car charging state tracking for hysteresis
        self._previous_car_charging: bool = False

        # Price interval tracking for threshold stability
        self._current_price_interval_start: datetime | None = None
        self._battery_threshold_snapshot: float | None = None
        self._last_config_hash: int | None = None  # Track config changes for threshold updates

        # Manual override tracking
        self._manual_overrides: dict[str, dict[str, Any] | None] = {
            "battery_grid_charging": None,
            "car_grid_charging": None,
        }
        self._last_price_timeline: list[tuple[datetime, datetime, float]] | None = None
        self._last_price_timeline_generated_at: datetime | None = None
        self._price_timeline_max_age = timedelta(hours=PRICE_TIMELINE_MAX_AGE_HOURS)

        # Car peak limit tracking (15-minute hold after 5 minutes of sustained peak exceedance)
        self._car_peak_limited_until: datetime | None = None
        self._car_peak_limit_started_at: datetime | None = None

        # Average threshold hysteresis tracking
        self._average_threshold_valid_count: int = 0  # Count of consecutive valid calculations
        self._average_threshold_enabled: bool = False  # Whether average threshold is active

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=30),  # Maximum 30s updates (minimum 10s via entity changes)
        )

        self._setup_entity_listeners()

    def _is_data_available(self, data: dict[str, Any]) -> bool:
        """Check if critical price data is available for decisions."""
        price_available = data.get("current_price") is not None
        price_range_available = (
            data.get("highest_price") is not None
            and data.get("lowest_price") is not None
        )
        # Allow price analysis to mark availability once decision engine runs
        price_analysis_available = data.get("price_analysis", {}).get("data_available")
        return bool(price_available and (price_range_available or price_analysis_available))

    def _setup_entity_listeners(self):
        """Set up listeners for entity state changes."""
        entities_to_track = []

        # Price entities
        for entity_key in [CONF_CURRENT_PRICE_ENTITY, CONF_HIGHEST_PRICE_ENTITY,
                          CONF_LOWEST_PRICE_ENTITY, CONF_NEXT_PRICE_ENTITY]:
            if self.config.get(entity_key):
                entities_to_track.append(self.config[entity_key])

        # Battery entities
        if self.config.get(CONF_BATTERY_SOC_ENTITIES):
            entities_to_track.extend(self.config[CONF_BATTERY_SOC_ENTITIES])

        # Power entities
        for entity_key in [CONF_SOLAR_PRODUCTION_ENTITY, CONF_HOUSE_CONSUMPTION_ENTITY, CONF_CAR_CHARGING_POWER_ENTITY, CONF_MONTHLY_GRID_PEAK_ENTITY, CONF_GRID_POWER_ENTITY]:
            if self.config.get(entity_key):
                entities_to_track.append(self.config[entity_key])

        if self.phase_mode == PHASE_MODE_THREE and self.phase_configs:
            for phase_config in self.phase_configs.values():
                for entity_key in (
                    CONF_PHASE_SOLAR_ENTITY,
                    CONF_PHASE_CONSUMPTION_ENTITY,
                    CONF_PHASE_CAR_ENTITY,
                    CONF_PHASE_BATTERY_POWER_ENTITY,
                ):
                    phase_entity = phase_config.get(entity_key)
                    if phase_entity:
                        entities_to_track.append(phase_entity)

        if entities_to_track:
            async_track_state_change_event(
                self.hass, entities_to_track, self._handle_entity_change
            )

    def _resolve_transport_cost(
        self,
        transport_lookup: list[dict[str, Any]] | None,
        start_time_utc: datetime,
        reference_now: datetime | None = None,
    ) -> float | None:
        """Resolve transport cost for a specific timestamp.

        Args:
            transport_lookup: List of transport cost entries with timestamps
            start_time_utc: The timestamp to resolve cost for
            reference_now: Reference time for "now" (defaults to current UTC time)

        Returns:
            Transport cost in €/kWh, or None if not available
        """
        if not transport_lookup:
            return None

        if reference_now is None:
            reference_now = dt_util.utcnow()

        # For future times, try to reuse the value from the same hour a week ago
        if start_time_utc > reference_now:
            week_ago = start_time_utc - timedelta(days=7)
            cost_from_pattern: float | None = None
            for entry in transport_lookup:
                entry_cost = entry.get("cost")
                if entry_cost is None:
                    continue
                entry_start_str = entry.get("start")
                if entry_start_str is None:
                    cost_from_pattern = float(entry_cost)
                    continue
                entry_start = dt_util.parse_datetime(entry_start_str)
                if entry_start is None:
                    continue
                entry_start_utc = dt_util.as_utc(entry_start)
                if entry_start_utc <= week_ago:
                    cost_from_pattern = float(entry_cost)
                else:
                    break

            if cost_from_pattern is not None:
                return cost_from_pattern

        # Otherwise use the most recent cost we have
        cost: float | None = None
        for entry in transport_lookup:
            entry_cost = entry.get("cost")
            if entry_cost is None:
                continue
            entry_start_str = entry.get("start")
            if entry_start_str is None:
                cost = float(entry_cost)
                continue
            entry_start = dt_util.parse_datetime(entry_start_str)
            if entry_start is None:
                continue
            entry_start_utc = dt_util.as_utc(entry_start)
            if entry_start_utc <= start_time_utc:
                cost = float(entry_cost)
            else:
                break

        return cost

    def _resolve_override_targets(self, target: str) -> tuple[str, ...]:
        """Resolve override target string into coordinator keys.

        Args:
            target: Target identifier (battery, car, both, charger_limit, grid_setpoint, all)

        Returns:
            Tuple of coordinator key strings that match the target
        """
        mapping = {
            "battery": ("battery_grid_charging",),
            "car": ("car_grid_charging",),
            "both": ("battery_grid_charging", "car_grid_charging"),
            "charger_limit": ("charger_limit",),
            "grid_setpoint": ("grid_setpoint",),
            "all": ("battery_grid_charging", "car_grid_charging", "charger_limit", "grid_setpoint"),
        }
        return mapping.get(target, ())

    async def async_set_manual_override(
        self,
        target: str,
        value: bool | None,
        duration: timedelta | None,
        reason: str | None,
        charger_limit: int | None = None,
        grid_setpoint: int | None = None,
    ) -> None:
        """Apply a manual override for battery or car decisions, charger limit, or grid setpoint."""
        now = dt_util.utcnow()
        expires_at = now + duration if duration is not None else None

        # Apply boolean overrides (battery/car charging) only if value is provided
        if value is not None:
            manual_reason = reason or ("force charge" if value else "force wait")
            for coordinator_key in self._resolve_override_targets(target):
                # Only apply boolean overrides to actual boolean keys
                if coordinator_key in ("battery_grid_charging", "car_grid_charging"):
                    self._manual_overrides[coordinator_key] = {
                        "value": value,
                        "reason": manual_reason,
                        "expires_at": expires_at,
                        "set_at": now,
                    }
                    _LOGGER.info(
                        "Manual override set for %s → %s (expires %s)",
                        coordinator_key,
                        value,
                        expires_at.isoformat() if expires_at else "never",
                    )

        # Apply numeric overrides (charger_limit and grid_setpoint)
        if charger_limit is not None:
            self._manual_overrides["charger_limit"] = {
                "value": charger_limit,
                "reason": reason or "Manual charger limit override",
                "expires_at": expires_at,
                "set_at": now,
            }
            _LOGGER.info(
                "Manual override set for charger_limit → %dW (expires %s)",
                charger_limit,
                expires_at.isoformat() if expires_at else "never",
            )

        if grid_setpoint is not None:
            self._manual_overrides["grid_setpoint"] = {
                "value": grid_setpoint,
                "reason": reason or "Manual grid setpoint override",
                "expires_at": expires_at,
                "set_at": now,
            }
            _LOGGER.info(
                "Manual override set for grid_setpoint → %dW (expires %s)",
                grid_setpoint,
                expires_at.isoformat() if expires_at else "never",
            )

    async def async_clear_manual_override(self, target: str | None = None) -> None:
        """Clear manual overrides for the given target (or all)."""
        effective_target = target or "all"
        for coordinator_key in self._resolve_override_targets(effective_target):
            if self._manual_overrides.get(coordinator_key):
                _LOGGER.info("Manual override cleared for %s", coordinator_key)
            self._manual_overrides[coordinator_key] = None

    def _update_peak_limit_state(self, data: dict[str, Any]) -> None:
        """Track 15-minute car charging limit after 5 minutes of sustained peak exceedance."""
        now = dt_util.utcnow()

        # Check if hold period expired
        if self._car_peak_limited_until and now >= self._car_peak_limited_until:
            self._car_peak_limited_until = None
            _LOGGER.debug("Car peak limit hold expired")

        # Calculate threshold (configurable % over effective peak)
        monthly_peak = data.get("monthly_grid_peak")
        base_grid_setpoint = self.config.get(CONF_BASE_GRID_SETPOINT, DEFAULT_BASE_GRID_SETPOINT)
        effective_peak = max(monthly_peak or 0, base_grid_setpoint)
        peak_threshold = effective_peak * PEAK_THRESHOLD_MULTIPLIER if effective_peak > 0 else None

        # Check current state
        grid_power = data.get("grid_power")
        car_power = data.get("car_charging_power") or 0.0
        min_car_threshold = self.config.get(
            CONF_MIN_CAR_CHARGING_THRESHOLD, DEFAULT_MIN_CAR_CHARGING_THRESHOLD
        )
        car_charging = car_power >= min_car_threshold
        currently_limited = bool(self._car_peak_limited_until and now < self._car_peak_limited_until)

        # Only monitor if car is charging, not already limited, and we have valid data
        if peak_threshold and grid_power is not None and car_charging and not currently_limited:
            # Grid power convention: positive = import, negative = export
            grid_import = max(0.0, float(grid_power))

            if grid_import > peak_threshold:
                # Start or continue monitoring
                if self._car_peak_limit_started_at is None:
                    self._car_peak_limit_started_at = now
                    _LOGGER.debug(
                        "Grid import %.0fW > %.0fW: starting %d-minute monitoring",
                        grid_import, peak_threshold, PEAK_MONITORING_DURATION_MINUTES
                    )
                else:
                    # Check if sustained for configured duration
                    exceed_duration = now - self._car_peak_limit_started_at
                    if exceed_duration >= timedelta(minutes=PEAK_MONITORING_DURATION_MINUTES):
                        self._car_peak_limited_until = now + timedelta(minutes=PEAK_LIMIT_DURATION_MINUTES)
                        self._car_peak_limit_started_at = None  # Reset monitoring
                        _LOGGER.info(
                            "Grid import %.0fW exceeded %.0fW for %d minutes. "
                            "Halving car charger limit for %d minutes.",
                            grid_import, peak_threshold,
                            PEAK_MONITORING_DURATION_MINUTES, PEAK_LIMIT_DURATION_MINUTES
                        )
                        currently_limited = True
            else:
                # Dropped below threshold - reset monitoring
                if self._car_peak_limit_started_at is not None:
                    _LOGGER.debug(
                        "Grid import back below threshold (%.0fW <= %.0fW) - monitoring reset",
                        grid_import, peak_threshold
                    )
                self._car_peak_limit_started_at = None
        else:
            # Not eligible for monitoring - reset
            self._car_peak_limit_started_at = None

        # Expose minimal data
        data["car_peak_limited"] = currently_limited
        data["car_peak_limit_threshold"] = peak_threshold

    def _apply_manual_overrides(
        self, decision: dict[str, Any]
    ) -> tuple[dict[str, Any], set[str]]:
        """Apply active manual overrides to the decision payload."""
        now = dt_util.utcnow()
        overrides_info: dict[str, Any] = {}
        base_trace = decision.get("strategy_trace") or []
        augmented_trace = list(base_trace)
        changed_targets: set[str] = set()

        # Collect expired keys to delete after iteration
        expired_keys: list[str] = []

        for coordinator_key, override in self._manual_overrides.items():
            if not override:
                continue

            expires_at: datetime | None = override.get("expires_at")
            if expires_at and expires_at <= now:
                expired_keys.append(coordinator_key)
                continue

            override_value = override["value"]
            manual_reason: str = override.get("reason", "Manual override")

            # Handle numeric overrides (charger_limit, grid_setpoint)
            if coordinator_key in ("charger_limit", "grid_setpoint"):
                previous_value = decision.get(coordinator_key)
                decision[coordinator_key] = override_value
                if previous_value != override_value:
                    changed_targets.add(coordinator_key)

                reason_key = f"{coordinator_key}_reason"
                existing_reason = decision.get(reason_key)
                if existing_reason:
                    decision[reason_key] = f"{existing_reason} (override: {manual_reason})"
                else:
                    decision[reason_key] = f"Manual override: {manual_reason}"

                overrides_info[coordinator_key] = {
                    "value": override_value,
                    "reason": manual_reason,
                    "set_at": override.get("set_at").isoformat() if override.get("set_at") else None,
                    "expires_at": expires_at.isoformat() if expires_at else None,
                }

            # Handle boolean overrides (battery_grid_charging, car_grid_charging)
            else:
                if not manual_reason or manual_reason == "Manual override":
                    manual_reason = "Manual override to charge" if override_value else "Manual override to wait"

                previous_value = decision.get(coordinator_key)
                decision[coordinator_key] = override_value
                if previous_value != override_value:
                    changed_targets.add(coordinator_key)

                reason_key = f"{coordinator_key}_reason"
                existing_reason = decision.get(reason_key)
                if existing_reason:
                    decision[reason_key] = f"{existing_reason} (override: {manual_reason})"
                else:
                    decision[reason_key] = f"Manual override: {manual_reason}"

                overrides_info[coordinator_key] = {
                    "value": override_value,
                    "reason": manual_reason,
                    "set_at": override.get("set_at").isoformat() if override.get("set_at") else None,
                    "expires_at": expires_at.isoformat() if expires_at else None,
                }

                augmented_trace.append(
                    {
                        "strategy": "ManualOverride",
                        "priority": -1,
                        "should_charge": override_value,
                        "reason": manual_reason,
                        "target": coordinator_key,
                    }
                )

        # Clean up expired overrides after iteration
        for key in expired_keys:
            del self._manual_overrides[key]
            _LOGGER.debug("Removed expired manual override: %s", key)

        if overrides_info:
            decision["manual_overrides"] = overrides_info
            decision["strategy_trace"] = augmented_trace
        else:
            decision.setdefault("manual_overrides", {})

        return decision, changed_targets

    def _build_price_timeline(
        self,
        prices_today: dict[str, Any] | None,
        prices_tomorrow: dict[str, Any] | None,
        transport_lookup: list[dict[str, Any]] | None,
        current_transport_cost: float | None,
        now: datetime,
    ) -> list[tuple[datetime, datetime, float]]:
        """Build a chronological price timeline with fully resolved intervals."""
        multiplier = self.config.get(
            CONF_PRICE_ADJUSTMENT_MULTIPLIER, DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER
        )
        offset = self.config.get(
            CONF_PRICE_ADJUSTMENT_OFFSET, DEFAULT_PRICE_ADJUSTMENT_OFFSET
        )

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
                        except Exception:
                            end_time = None

                    # Skip intervals that have completely ended
                    if end_time is not None:
                        if end_time <= now:
                            continue
                    else:
                        # Assume hourly interval if no end provided
                        if start_time_utc < now - timedelta(hours=PRICE_INTERVAL_LOOKBACK_HOURS):
                            continue

                    price_value = None
                    for key in ("value", "value_exc_vat", "price"):
                        value = interval.get(key)
                        if isinstance(value, (int, float)):
                            price_value = float(value)
                            break
                        if isinstance(value, str):
                            try:
                                price_value = float(value)
                                break
                            except (ValueError, TypeError):
                                continue

                    if price_value is None:
                        continue

                    # Validate price is in reasonable range (assuming €/MWh from Nord Pool)
                    if not PRICE_VALUE_MIN_EUR_MWH <= price_value <= PRICE_VALUE_MAX_EUR_MWH:
                        _LOGGER.warning(
                            "Suspicious price value %.2f €/MWh outside expected range [%d, %d], skipping interval",
                            price_value, PRICE_VALUE_MIN_EUR_MWH, PRICE_VALUE_MAX_EUR_MWH
                        )
                        continue

                    price_kwh = price_value / 1000
                    adjusted_price = (price_kwh * multiplier) + offset

                    transport_cost = self._resolve_transport_cost(
                        transport_lookup, start_time_utc, reference_now=now
                    )
                    if transport_cost is None:
                        transport_cost = (
                            current_transport_cost
                            if current_transport_cost is not None
                            else 0.0
                        )

                    final_price = adjusted_price + transport_cost
                    future_intervals.append((start_time_utc, end_time, final_price))

                except (ValueError, TypeError, KeyError, AttributeError) as err:
                    _LOGGER.debug(
                        "Expected error processing interval for price timeline: %s", err
                    )
                    continue
                except Exception as err:
                    # Don't catch system exceptions
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

        timeline: list[tuple[datetime, datetime, float]] = []
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

            timeline.append((start_time, interval_end, price))

        return timeline

    @callback
    def _handle_entity_change(self, event: Event) -> None:
        """Handle entity state changes with minimum update interval.

        Args:
            event: Home Assistant state change event
        """
        # Note: This is a callback, so we can't use async lock directly
        # The throttling is handled by checking _last_entity_update timestamp
        entity_id = event.data.get("entity_id")
        _LOGGER.debug("Entity changed: %s", entity_id)

        # Trigger immediate updates for critical entities
        critical_entities = {
            self.config.get(CONF_CURRENT_PRICE_ENTITY),
            self.config.get(CONF_SOLAR_PRODUCTION_ENTITY),
            self.config.get(CONF_HOUSE_CONSUMPTION_ENTITY),
            self.config.get(CONF_CAR_CHARGING_POWER_ENTITY),
        }

        if self.phase_mode == PHASE_MODE_THREE and self.phase_configs:
            for phase_config in self.phase_configs.values():
                for entity_key in (
                    CONF_PHASE_SOLAR_ENTITY,
                    CONF_PHASE_CONSUMPTION_ENTITY,
                    CONF_PHASE_CAR_ENTITY,
                ):
                    phase_entity = phase_config.get(entity_key)
                    if phase_entity:
                        critical_entities.add(phase_entity)

        critical_entities.discard(None)

        # Trigger updates for battery SOC changes (any configured battery)
        battery_entities = self.config.get(CONF_BATTERY_SOC_ENTITIES, [])

        if entity_id in critical_entities or entity_id in battery_entities:
            # Use async task to avoid blocking the callback
            # Note: Throttling is handled atomically in _async_handle_throttled_update
            # to prevent race conditions from multiple rapid events
            self.hass.async_create_task(self._async_handle_throttled_update(entity_id))

    async def _async_handle_throttled_update(self, entity_id: str) -> None:
        """Handle entity update with atomic throttling check."""
        async with self._update_lock:
            now = dt_util.utcnow()

            # Apply minimum interval throttling (atomic check-and-set)
            if (self._last_entity_update is None or
                now - self._last_entity_update >= self._min_update_interval):

                self._last_entity_update = now
                # Schedule refresh outside the lock to avoid blocking
                await self.async_request_refresh()
                _LOGGER.debug("Entity update triggered for %s (throttled to %ds minimum)",
                            entity_id, self._min_update_interval.total_seconds())
            else:
                time_remaining = (self._last_entity_update + self._min_update_interval - now).total_seconds()
                _LOGGER.debug("Entity update skipped for %s (throttled, %.1fs remaining)",
                            entity_id, time_remaining)

    def _get_current_price_interval_start(self) -> datetime:
        """Get the start time of the current 15-minute price interval."""
        now = dt_util.now()
        # Round down to nearest 15 minutes
        minutes = (now.minute // 15) * 15
        return now.replace(minute=minutes, second=0, microsecond=0)

    def _clean_expired_nordpool_cache(self) -> None:
        """Remove expired entries from Nord Pool cache based on TTL and size limit.

        Evicts cache entries older than NORDPOOL_CACHE_TTL_MINUTES to prevent
        stale data from persisting when cache doesn't fill up to max size.
        Also enforces max size limit using LRU eviction.
        """
        now = dt_util.utcnow()
        ttl = timedelta(minutes=NORDPOOL_CACHE_TTL_MINUTES)

        # First, remove expired entries
        expired_keys = [
            key for key, (_, timestamp) in self._nordpool_cache.items()
            if now - timestamp > ttl
        ]
        for key in expired_keys:
            del self._nordpool_cache[key]
            _LOGGER.debug("Evicted expired Nord Pool cache entry: %s", key)

        # Then, enforce size limit using LRU (oldest timestamp first)
        if len(self._nordpool_cache) > NORDPOOL_CACHE_MAX_SIZE:
            sorted_items = sorted(
                self._nordpool_cache.items(),
                key=lambda x: x[1][1]  # Sort by timestamp
            )
            entries_to_remove = len(self._nordpool_cache) - NORDPOOL_CACHE_MAX_SIZE
            for key, _ in sorted_items[:entries_to_remove]:
                del self._nordpool_cache[key]
                _LOGGER.debug("Evicted old Nord Pool cache entry (size limit): %s", key)

    def _update_battery_threshold_snapshot_if_needed(self, price_threshold: float | None) -> None:
        """Update battery threshold snapshot when entering a new price interval or config changes."""
        if price_threshold is None:
            return

        current_interval = self._get_current_price_interval_start()

        # Calculate config hash to detect changes
        config_hash = hash((
            self.config.get(CONF_PRICE_THRESHOLD),
            self.config.get(CONF_USE_AVERAGE_THRESHOLD),
            self.config.get(CONF_PRICE_ADJUSTMENT_MULTIPLIER),
            self.config.get(CONF_PRICE_ADJUSTMENT_OFFSET),
        ))

        # Update if:
        # 1. First interval
        # 2. New interval started
        # 3. Configuration changed (force update even mid-interval)
        config_changed = self._last_config_hash is not None and config_hash != self._last_config_hash

        if (self._current_price_interval_start is None or
            current_interval != self._current_price_interval_start or
            config_changed):

            self._current_price_interval_start = current_interval
            self._battery_threshold_snapshot = price_threshold
            self._last_config_hash = config_hash

            reason = "config changed" if config_changed else "new interval"
            _LOGGER.debug(
                "Battery threshold snapshot updated (%s) at %s: %.4f€/kWh",
                reason,
                current_interval.strftime("%H:%M"),
                price_threshold
            )

    async def _async_update_data(self) -> dict[str, Any]:
        """Update data via library."""
        try:
            # Clean expired cache entries periodically
            self._clean_expired_nordpool_cache()

            data = await self._fetch_all_data()

            # Determine the current price threshold (with dynamic/average logic)
            use_average = self.config.get(CONF_USE_AVERAGE_THRESHOLD, DEFAULT_USE_AVERAGE_THRESHOLD)
            average_threshold = data.get("average_threshold")

            if use_average and average_threshold is not None:
                current_threshold = average_threshold
            else:
                current_threshold = self.config.get(CONF_PRICE_THRESHOLD, DEFAULT_PRICE_THRESHOLD)

            # Update battery threshold snapshot if we've entered a new 15-min interval
            self._update_battery_threshold_snapshot_if_needed(current_threshold)

            # Add previous car charging state for hysteresis logic
            data["previous_car_charging"] = self._previous_car_charging

            # Pass the stable threshold snapshot to decision engine
            if self._battery_threshold_snapshot is not None:
                data["battery_stable_threshold"] = self._battery_threshold_snapshot

            # Update peak import limit state based on current grid power
            self._update_peak_limit_state(data)

            charging_decision = await self.decision_engine.evaluate_charging_decision(data)
            charging_decision, override_targets = self._apply_manual_overrides(charging_decision)

            if override_targets:
                charging_decision = self.decision_engine.recalculate_after_override(
                    data, charging_decision, override_targets
                )

            data.update(charging_decision)

            # Update previous car charging state
            self._previous_car_charging = charging_decision.get("car_grid_charging", False)

            # Check data availability and handle notifications
            await self._check_data_availability(data)

            return data

        except Exception as err:
            raise UpdateFailed(f"Error communicating with entities: {err}") from err

    async def _fetch_all_data(self) -> dict[str, Any]:
        """Fetch data from all configured entities."""
        data = {}

        # Price data
        data["current_price"] = await self._get_state_value(
            self.config.get(CONF_CURRENT_PRICE_ENTITY)
        )
        data["highest_price"] = await self._get_state_value(
            self.config.get(CONF_HIGHEST_PRICE_ENTITY)
        )
        data["lowest_price"] = await self._get_state_value(
            self.config.get(CONF_LOWEST_PRICE_ENTITY)
        )
        data["next_price"] = await self._get_state_value(
            self.config.get(CONF_NEXT_PRICE_ENTITY)
        )
        # Battery SOC data
        battery_soc_entities = self.config.get(CONF_BATTERY_SOC_ENTITIES, [])
        battery_soc_values = []

        _LOGGER.debug("Battery SOC entities configured: %s", battery_soc_entities)

        for entity_id in battery_soc_entities:
            soc = await self._get_state_value(entity_id)
            state = self.hass.states.get(entity_id)
            _LOGGER.debug("Battery entity %s: state=%s, parsed_value=%s",
                         entity_id, state.state if state else "missing", soc)
            if soc is not None:
                # Validate and normalize battery SOC
                if 0 <= soc <= BATTERY_SOC_DECIMAL_THRESHOLD:
                    # SOC appears to be in decimal format (0-1), convert to percentage
                    _LOGGER.info(
                        "Battery SOC for %s appears to be decimal (%.3f), converting to percentage (%.1f%%)",
                        entity_id, soc, soc * 100
                    )
                    soc = soc * 100
                elif not 0 <= soc <= 100:
                    # SOC is outside valid range
                    _LOGGER.error(
                        "Invalid battery SOC value for %s: %.2f (expected 0-100%%), excluding from calculations",
                        entity_id, soc
                    )
                    continue

                battery_soc_values.append({"entity_id": entity_id, "soc": soc})
            else:
                _LOGGER.warning("Battery entity %s is unavailable - excluding from calculations", entity_id)

        data["battery_soc"] = battery_soc_values
        _LOGGER.debug("Final battery SOC data: %s", battery_soc_values)

        # Map batteries to phases (always available for diagnostics)
        battery_capacities_cfg = self.config.get(CONF_BATTERY_CAPACITIES, {})
        phase_capacity_map: dict[str, float] = {phase_id: 0.0 for phase_id in PHASE_IDS}
        phase_batteries: dict[str, list[dict[str, Any]]] = {phase_id: [] for phase_id in PHASE_IDS}
        battery_details: list[dict[str, Any]] = []

        default_phase = PHASE_IDS[0]
        for battery in battery_soc_values:
            entity_id = battery["entity_id"]
            assigned_phases = self.battery_phase_assignments.get(entity_id)
            if assigned_phases:
                valid_phases = [phase for phase in assigned_phases if phase in PHASE_IDS]
                assigned_phases = valid_phases or [default_phase]
            else:
                assigned_phases = [default_phase]

            capacity = battery_capacities_cfg.get(entity_id)
            if capacity is None or capacity <= 0:
                _LOGGER.warning(
                    "Battery capacity not configured or invalid for %s - using fallback weight %.1f kWh. "
                    "Configure capacity in integration options for accurate weighted SOC calculations.",
                    entity_id, BATTERY_CAPACITY_FALLBACK_WEIGHT
                )
                capacity = BATTERY_CAPACITY_FALLBACK_WEIGHT  # fallback weight when capacity not provided

            battery_entry = {
                "entity_id": entity_id,
                "soc": battery["soc"],
                "capacity": capacity,
                "phases": assigned_phases,
            }
            battery_details.append(battery_entry)

            phase_share = (capacity or 0) / len(assigned_phases) if assigned_phases else 0
            for phase_id in assigned_phases:
                phase_capacity_map.setdefault(phase_id, 0.0)
                phase_capacity_map[phase_id] += phase_share
                phase_batteries.setdefault(phase_id, []).append(dict(battery_entry))

        data["battery_details"] = battery_details
        data["phase_capacity_map"] = phase_capacity_map
        data["phase_batteries"] = phase_batteries

        # Power data
        data["phase_mode"] = self.phase_mode
        phase_details: dict[str, Any] = {}

        if self.phase_mode == PHASE_MODE_THREE and self.phase_configs:
            total_solar_value = 0.0
            total_consumption_value = 0.0
            total_car_value = 0.0
            solar_present = False
            consumption_present = False
            car_present = False

            for phase_id in PHASE_IDS:
                phase_config = self.phase_configs.get(phase_id)
                if not phase_config:
                    continue

                phase_name = phase_config.get(
                    CONF_PHASE_NAME, DEFAULT_PHASE_NAMES.get(phase_id, phase_id)
                )

                # Solar: Read actual per-phase production from sensor
                solar_entity = phase_config.get(CONF_PHASE_SOLAR_ENTITY)
                solar_val = await self._get_state_value(solar_entity)

                if solar_val is not None and solar_val > 0:
                    solar_present = True
                    total_solar_value += solar_val

                # Consumption: from per-phase sensor (required)
                consumption_entity = phase_config.get(CONF_PHASE_CONSUMPTION_ENTITY)
                consumption_val = await self._get_state_value(consumption_entity)
                if consumption_val is not None:
                    consumption_present = True
                    total_consumption_value += consumption_val

                # Car: from per-phase sensor (optional)
                car_entity = phase_config.get(CONF_PHASE_CAR_ENTITY)
                car_val = await self._get_state_value(car_entity)
                if car_val is not None:
                    car_present = True
                    total_car_value += car_val

                # Battery power: from per-phase sensor (optional, negative = charging)
                battery_power_entity = phase_config.get(CONF_PHASE_BATTERY_POWER_ENTITY)
                battery_power_val = await self._get_state_value(battery_power_entity)

                # Calculate phase surplus - consistent with single-phase mode
                # Allow solar_val to be 0 (not just > 0)
                phase_surplus = None
                if solar_val is not None and consumption_val is not None:
                    phase_surplus = max(0, solar_val - consumption_val)

                phase_details[phase_id] = {
                    "name": phase_name,
                    "solar_production": solar_val,
                    "house_consumption": consumption_val,
                    "car_charging_power": car_val,
                    "battery_power": battery_power_val,  # Actual battery power (W, negative = charging)
                    "solar_surplus": phase_surplus,
                    "has_car_sensor": car_entity is not None,
                    "has_battery_power_sensor": battery_power_entity is not None,
                }

            if phase_details:
                data["phase_details"] = phase_details
                _LOGGER.debug("Per-phase power snapshot: %s", phase_details)

            data["solar_production"] = total_solar_value if solar_present else None
            data["house_consumption"] = total_consumption_value if consumption_present else None

            if car_present:
                data["car_charging_power"] = total_car_value
            else:
                data["car_charging_power"] = await self._get_state_value(
                    self.config.get(CONF_CAR_CHARGING_POWER_ENTITY)
                )

            solar_total = data["solar_production"] or 0
            consumption_total = data["house_consumption"] or 0
            data["solar_surplus"] = max(0, solar_total - consumption_total)
            _LOGGER.debug(
                "Aggregated power totals: solar=%sW, consumption=%sW, surplus=%sW",
                data["solar_production"],
                data["house_consumption"],
                data["solar_surplus"],
            )
        else:
            # Single-phase mode
            solar_production = await self._get_state_value(
                self.config.get(CONF_SOLAR_PRODUCTION_ENTITY)
            )
            house_consumption = await self._get_state_value(
                self.config.get(CONF_HOUSE_CONSUMPTION_ENTITY)
            )

            data["solar_production"] = solar_production
            data["house_consumption"] = house_consumption

            if solar_production is not None and house_consumption is not None:
                data["solar_surplus"] = max(0, solar_production - house_consumption)
            elif solar_production is not None:
                data["solar_surplus"] = solar_production
            else:
                data["solar_surplus"] = 0

            _LOGGER.debug(
                "Solar production: %sW, house consumption: %sW, available surplus: %sW",
                solar_production,
                house_consumption,
                data["solar_surplus"],
            )

            data["car_charging_power"] = await self._get_state_value(
                self.config.get(CONF_CAR_CHARGING_POWER_ENTITY)
            )

        data["monthly_grid_peak"] = await self._get_state_value(
            self.config.get(CONF_MONTHLY_GRID_PEAK_ENTITY)
        )

        data["grid_power"] = await self._get_state_value(
            self.config.get(CONF_GRID_POWER_ENTITY)
        )

        # Preserve car charging locked threshold across updates (for threshold continuity)
        data["car_charging_locked_threshold"] = self.data.get("car_charging_locked_threshold") if self.data else None

        # Preserve car permissive mode state across updates (controlled via switch entity)
        if self.data:
            data["car_permissive_mode_active"] = bool(self.data.get("car_permissive_mode_active", False))
        else:
            data["car_permissive_mode_active"] = False



        data["transport_cost"] = await self._get_state_value(
            self.config.get(CONF_TRANSPORT_COST_ENTITY)
        )

        # Fetch full day price data if Nord Pool config entry is configured
        # This retrieves all price intervals for today and tomorrow at whatever
        # granularity Nord Pool provides (currently 15-min, but flexible)
        nordpool_config_entry = self.config.get(CONF_NORDPOOL_CONFIG_ENTRY)
        if nordpool_config_entry:
            data["nordpool_prices_today"] = await self._fetch_nordpool_prices(nordpool_config_entry, "today")
            data["nordpool_prices_tomorrow"] = await self._fetch_nordpool_prices(nordpool_config_entry, "tomorrow")
        else:
            data["nordpool_prices_today"] = None
            data["nordpool_prices_tomorrow"] = None

        transport_lookup, transport_status = await self._get_transport_cost_lookup(
            data.get("transport_cost")
        )
        data["transport_cost_lookup"] = transport_lookup
        data["transport_cost_status"] = transport_status

        # Calculate average threshold if enabled
        average_threshold = self._calculate_average_threshold(
            data.get("nordpool_prices_today"),
            data.get("nordpool_prices_tomorrow"),
            transport_lookup
        )
        data["average_threshold"] = average_threshold

        car_permissive_multiplier = self.config.get(
            CONF_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
            DEFAULT_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
        )

        # Calculate if we have at least 2 hours of low prices ahead for car charging
        data["has_min_charging_window"] = self._check_minimum_charging_window(
            data.get("nordpool_prices_today"),
            data.get("nordpool_prices_tomorrow"),
            transport_lookup,
            data.get("transport_cost"),
            average_threshold,
            data.get("car_permissive_mode_active", False),
            car_permissive_multiplier,
        )

        data["forecast_summary"] = self._calculate_forecast_summary(
            data.get("nordpool_prices_today"),
            data.get("nordpool_prices_tomorrow"),
            transport_lookup,
            data.get("transport_cost"),
            average_threshold,
        )

        # Entity status tracking for diagnostic visibility
        data["entity_status"] = self.get_all_entity_statuses()

        return data

    async def _get_state_value(self, entity_id: str | None) -> float | None:
        """Get numeric state value from entity."""
        if not entity_id:
            return None

        state = self.hass.states.get(entity_id)
        if not state or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None

        try:
            return float(state.state)
        except (ValueError, TypeError):
            # Use debug level for expected conversion failures to reduce log noise
            if _LOGGER.isEnabledFor(logging.DEBUG):
                _LOGGER.debug("Could not convert state to float: %s = %s", entity_id, state.state)
            return None

    def _get_entity_status(self, entity_id: str | None, is_required: bool = False) -> dict[str, Any]:
        """Get detailed status for a configured entity.

        Args:
            entity_id: The entity ID to check
            is_required: Whether this entity is required for the integration to function

        Returns:
            Dict with entity status details
        """
        if not entity_id:
            return {"configured": False, "status": "not_configured", "is_required": is_required}

        state = self.hass.states.get(entity_id)
        if not state:
            return {
                "configured": True,
                "entity_id": entity_id,
                "status": "missing",
                "is_required": is_required,
                "reason": "Entity not found in Home Assistant",
            }

        if state.state == STATE_UNAVAILABLE:
            return {
                "configured": True,
                "entity_id": entity_id,
                "status": "unavailable",
                "is_required": is_required,
                "reason": "Entity is unavailable",
            }

        if state.state == STATE_UNKNOWN:
            return {
                "configured": True,
                "entity_id": entity_id,
                "status": "unknown",
                "is_required": is_required,
                "reason": "Entity state is unknown",
            }

        # Try to parse as float for numeric entities
        try:
            float(state.state)
            return {
                "configured": True,
                "entity_id": entity_id,
                "status": "available",
                "is_required": is_required,
                "current_value": state.state,
            }
        except (ValueError, TypeError):
            # Non-numeric but valid state
            return {
                "configured": True,
                "entity_id": entity_id,
                "status": "available",
                "is_required": is_required,
                "current_value": state.state,
            }

    def get_all_entity_statuses(self) -> dict[str, Any]:
        """Get status of all configured entities, organized by category.

        Returns:
            Dict with entity statuses organized by category
        """
        phase_mode = self.config.get(CONF_PHASE_MODE, PHASE_MODE_SINGLE)

        # Price entities (required)
        price_entities = {
            "current_price": self._get_entity_status(
                self.config.get(CONF_CURRENT_PRICE_ENTITY), is_required=True
            ),
            "highest_price": self._get_entity_status(
                self.config.get(CONF_HIGHEST_PRICE_ENTITY), is_required=True
            ),
            "lowest_price": self._get_entity_status(
                self.config.get(CONF_LOWEST_PRICE_ENTITY), is_required=True
            ),
            "next_price": self._get_entity_status(
                self.config.get(CONF_NEXT_PRICE_ENTITY), is_required=False
            ),
        }

        # Battery entities (optional but tracked individually)
        battery_entities = {}
        for entity_id in self.config.get(CONF_BATTERY_SOC_ENTITIES, []):
            battery_entities[entity_id] = self._get_entity_status(entity_id, is_required=False)

        # Power entities - depends on phase mode
        power_entities = {}
        if phase_mode == PHASE_MODE_THREE:
            # Three-phase mode - check phase entities
            phases_config = self.config.get(CONF_PHASES, {})
            for phase_id in PHASE_IDS:
                phase_config = phases_config.get(phase_id, {})
                power_entities[f"{phase_id}_solar"] = self._get_entity_status(
                    phase_config.get(CONF_PHASE_SOLAR_ENTITY), is_required=False
                )
                power_entities[f"{phase_id}_consumption"] = self._get_entity_status(
                    phase_config.get(CONF_PHASE_CONSUMPTION_ENTITY), is_required=False
                )
                power_entities[f"{phase_id}_car"] = self._get_entity_status(
                    phase_config.get(CONF_PHASE_CAR_ENTITY), is_required=False
                )
                power_entities[f"{phase_id}_battery_power"] = self._get_entity_status(
                    phase_config.get(CONF_PHASE_BATTERY_POWER_ENTITY), is_required=False
                )
        else:
            # Single-phase mode
            power_entities["solar_production"] = self._get_entity_status(
                self.config.get(CONF_SOLAR_PRODUCTION_ENTITY), is_required=False
            )
            power_entities["house_consumption"] = self._get_entity_status(
                self.config.get(CONF_HOUSE_CONSUMPTION_ENTITY), is_required=False
            )
            power_entities["car_charging_power"] = self._get_entity_status(
                self.config.get(CONF_CAR_CHARGING_POWER_ENTITY), is_required=False
            )

        # Optional entities
        optional_entities = {
            "monthly_grid_peak": self._get_entity_status(
                self.config.get(CONF_MONTHLY_GRID_PEAK_ENTITY), is_required=False
            ),
            "transport_cost": self._get_entity_status(
                self.config.get(CONF_TRANSPORT_COST_ENTITY), is_required=False
            ),
            "grid_power": self._get_entity_status(
                self.config.get(CONF_GRID_POWER_ENTITY), is_required=False
            ),
        }

        # Calculate summary
        all_entities = []
        for category in [price_entities, battery_entities, power_entities, optional_entities]:
            all_entities.extend(category.values())

        configured_entities = [e for e in all_entities if e.get("configured")]
        available_count = sum(1 for e in configured_entities if e.get("status") == "available")
        unavailable_count = sum(1 for e in configured_entities if e.get("status") in ("unavailable", "unknown", "missing"))
        required_unavailable = [
            e.get("entity_id") for e in configured_entities
            if e.get("is_required") and e.get("status") in ("unavailable", "unknown", "missing")
        ]

        return {
            "price_entities": price_entities,
            "battery_entities": battery_entities,
            "power_entities": power_entities,
            "optional_entities": optional_entities,
            "summary": {
                "total_configured": len(configured_entities),
                "available": available_count,
                "unavailable": unavailable_count,
                "required_unavailable": required_unavailable,
                "all_required_available": len(required_unavailable) == 0,
            },
        }

    async def _fetch_nordpool_prices(self, config_entry_id: str, day: str) -> dict[str, Any] | None:
        """Fetch Nord Pool prices for a specific date using the service call.

        Args:
            config_entry_id: The Nord Pool config entry ID
            day: Either "today" or "tomorrow"

        Returns:
            Dict with raw price data organized by area, or None if unavailable.
            The response structure is:
            {
                "AREA_CODE": [
                    {"start": "ISO timestamp", "end": "ISO timestamp", "price": float},
                    ...
                ]
            }
            Note: Interval duration is determined by Nord Pool and extracted from timestamps.
        """
        # Check cache first - prices only update hourly, no need to fetch every 30s
        now = dt_util.utcnow()
        cache_key = f"{day}_{config_entry_id}"

        # Check cache with safe tuple unpacking
        if cache_key in self._nordpool_cache:
            cached_data, cached_time = self._nordpool_cache[cache_key]
            cache_age = now - cached_time
            # Cache for configured TTL (prices change hourly, tomorrow appears at 13:00)
            if cache_age < timedelta(minutes=NORDPOOL_CACHE_TTL_MINUTES):
                _LOGGER.debug("Using cached Nord Pool prices for %s (age: %.1f minutes)",
                            day, cache_age.total_seconds() / 60)
                return cached_data

        try:
            # Calculate the target date
            now_local = dt_util.now()
            if day == "today":
                target_date = now_local.date()
            elif day == "tomorrow":
                target_date = (now_local + timedelta(days=1)).date()
            else:
                _LOGGER.error("Invalid day parameter: %s (expected 'today' or 'tomorrow')", day)
                return None

            # Call the Nord Pool service with exponential backoff retry
            max_retries = 3
            response = None
            for attempt in range(max_retries):
                try:
                    response = await self.hass.services.async_call(
                        "nordpool",
                        "get_prices_for_date",
                        {
                            "config_entry": config_entry_id,
                            "date": target_date.isoformat()
                        },
                        blocking=True,
                        return_response=True
                    )
                    # Success - break out of retry loop
                    break
                except ValueError as err:
                    # Invalid parameters - don't retry
                    _LOGGER.warning("Nord Pool service call failed (invalid parameters) for %s: %s", day, err)
                    return None
                except TimeoutError as err:
                    if attempt < max_retries - 1:
                        wait_time = 2 ** attempt  # 1s, 2s, 4s
                        _LOGGER.warning(
                            "Nord Pool service timed out for %s, retrying in %ds (attempt %d/%d): %s",
                            day, wait_time, attempt + 1, max_retries, err
                        )
                        await asyncio.sleep(wait_time)
                    else:
                        _LOGGER.error("Nord Pool service timed out for %s after %d attempts: %s", day, max_retries, err)
                        return None
                except Exception as err:
                    if attempt < max_retries - 1:
                        wait_time = 2 ** attempt  # 1s, 2s, 4s
                        _LOGGER.warning(
                            "Nord Pool service call failed for %s, retrying in %ds (attempt %d/%d): %s",
                            day, wait_time, attempt + 1, max_retries, err
                        )
                        await asyncio.sleep(wait_time)
                    else:
                        _LOGGER.error("Nord Pool service call failed for %s after %d attempts: %s", day, max_retries, err)
                        return None

            # The service returns a dict with area-based price data
            if response and isinstance(response, dict):
                # Count total entries across all areas for logging
                total_entries = sum(len(v) for v in response.values() if isinstance(v, list))
                _LOGGER.debug("Fetched Nord Pool prices for %s (%s): %d entries across %d area(s)",
                            day, target_date.isoformat(), total_entries, len(response))

                # Evict oldest cache entry if cache is full
                if len(self._nordpool_cache) >= self._nordpool_cache_max_size:
                    # Defensive check: ensure cache is not empty before finding min
                    if self._nordpool_cache:
                        oldest_key = min(self._nordpool_cache.keys(),
                                       key=lambda k: self._nordpool_cache[k][1])
                        del self._nordpool_cache[oldest_key]
                        _LOGGER.debug("Evicted oldest Nord Pool cache entry: %s", oldest_key)

                # Cache the response with timestamp
                self._nordpool_cache[cache_key] = (response, dt_util.utcnow())

                return response
            else:
                _LOGGER.warning("Nord Pool service returned unexpected format for %s", day)
                return None

        except Exception as err:
            _LOGGER.error("Unexpected error fetching Nord Pool prices for %s: %s", day, err, exc_info=True)
            return None

    def _calculate_average_threshold(
        self,
        prices_today: dict[str, Any] | None,
        prices_tomorrow: dict[str, Any] | None,
        transport_lookup: list[dict[str, Any]] | None
    ) -> float | None:
        """Calculate average threshold from a minimum 24-hour rolling window.

        Uses future prices when available, but backfills with recent past prices
        to ensure at least 24 hours of data for a stable threshold. This prevents
        volatile thresholds when only a few hours of future data remain (e.g., late evening).

        Algorithm:
        1. Collect all available future prices (from now onwards)
        2. If < 24 hours of future data, backfill with recent past to reach 24h minimum
        3. Calculate average of combined window

        Prices include full adjustments (multiplier + offset + transport cost).

        Returns None if insufficient price data is available.
        """
        if not prices_today and not prices_tomorrow:
            return None

        now = dt_util.utcnow()
        MIN_HOURS = 24  # Minimum hours for stable average threshold

        # Storage for price tuples: (start_time, final_price)
        all_intervals: list[tuple[datetime, float]] = []

        # Get multiplier and offset for price adjustments
        multiplier = self.config.get(
            CONF_PRICE_ADJUSTMENT_MULTIPLIER, DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER
        )
        offset = self.config.get(
            CONF_PRICE_ADJUSTMENT_OFFSET, DEFAULT_PRICE_ADJUSTMENT_OFFSET
        )

        def process_intervals(intervals: list[dict[str, Any]], include_past: bool = False, include_future: bool = True) -> None:
            """Process intervals and add them to all_intervals list.

            Args:
                intervals: List of price intervals to process
                include_past: Include intervals that have ended (start_time < now)
                include_future: Include intervals that haven't ended (start_time >= now)
            """
            for interval in intervals:
                try:
                    start_time_str = interval.get("start")
                    if not start_time_str:
                        continue

                    start_time = dt_util.parse_datetime(start_time_str)
                    if start_time is None:
                        continue
                    start_time_utc = dt_util.as_utc(start_time)

                    # Filter based on time
                    is_past = start_time_utc < now
                    if is_past and not include_past:
                        continue
                    if not is_past and not include_future:
                        continue

                    # Extract price (try different keys like Nord Pool sensor does)
                    price_value = None
                    for key in ("value", "value_exc_vat", "price"):
                        value = interval.get(key)
                        if isinstance(value, (int, float)):
                            price_value = float(value)
                            break
                        if isinstance(value, str):
                            try:
                                price_value = float(value)
                                break
                            except (ValueError, TypeError):
                                continue

                    if price_value is None:
                        continue

                    # Convert from €/MWh to €/kWh
                    price_kwh = price_value / 1000

                    # Apply adjustments
                    adjusted_price = (price_kwh * multiplier) + offset

                    # Add transport cost
                    transport_cost = self._resolve_transport_cost(
                        transport_lookup, start_time_utc, reference_now=now
                    )
                    if transport_cost is None:
                        transport_cost = 0.0

                    final_price = adjusted_price + transport_cost
                    all_intervals.append((start_time_utc, final_price))

                except (ValueError, TypeError, KeyError) as err:
                    _LOGGER.debug("Expected error processing interval for average threshold: %s", err)
                    continue
                except Exception as err:
                    _LOGGER.warning(
                        "Unexpected error processing interval for average threshold: %s",
                        err, exc_info=True
                    )
                    continue

        def infer_interval_resolution() -> timedelta:
            """Infer typical Nord Pool interval duration from provided data.

            Dynamically detects whether Nord Pool is using 15-minute, 1-hour,
            or other interval lengths. Returns the minimum delta found, which
            represents the actual granularity of the data.
            """
            deltas: list[timedelta] = []

            def collect(intervals: list[dict[str, Any]]) -> None:
                timestamps: list[datetime] = []
                for interval in intervals:
                    start_time_str = interval.get("start")
                    if not start_time_str:
                        continue
                    start_time = dt_util.parse_datetime(start_time_str)
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

            # Default fallback: assume 15-minute intervals (most common modern resolution)
            return timedelta(minutes=15)

        def collect_past_intervals(max_count: int) -> list[tuple[datetime, float]]:
            """Collect recent past price intervals for backfilling.

            Args:
                max_count: Maximum number of past intervals to collect

            Returns:
                List of (timestamp, price) tuples in chronological order (oldest first)
            """
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

                    start_time = dt_util.parse_datetime(start_time_str)
                    if start_time is None:
                        continue
                    start_time_utc = dt_util.as_utc(start_time)

                    # Only include past intervals
                    if start_time_utc >= now:
                        continue

                    # Extract and process price
                    price_value = None
                    for key in ("value", "value_exc_vat", "price"):
                        value = interval.get(key)
                        if isinstance(value, (int, float)):
                            price_value = float(value)
                            break
                        if isinstance(value, str):
                            try:
                                price_value = float(value)
                                break
                            except (ValueError, TypeError):
                                continue

                    if price_value is None:
                        continue

                    # Convert from €/MWh to €/kWh and apply adjustments
                    price_kwh = price_value / 1000
                    adjusted_price = (price_kwh * multiplier) + offset
                    transport_cost = self._resolve_transport_cost(
                        transport_lookup, start_time_utc, reference_now=now
                    )
                    if transport_cost is None:
                        transport_cost = 0.0
                    final_price = adjusted_price + transport_cost

                    past_intervals.append((start_time_utc, final_price))
                except Exception:
                    continue

            # Sort by time (most recent first) and take what we need
            past_intervals.sort(key=lambda x: x[0], reverse=True)
            limited = past_intervals[:max_count]
            limited.reverse()  # Back to chronological order (oldest first)

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

        # Sort by time (oldest first)
        all_intervals.sort(key=lambda x: x[0])

        # Separate future intervals
        future_intervals = [(t, p) for t, p in all_intervals if t >= now]

        if not future_intervals:
            _LOGGER.warning("No future price intervals available for average threshold")
            return None

        # Estimate interval resolution (typically 15min or 1h)
        if len(future_intervals) >= 2:
            delta = future_intervals[1][0] - future_intervals[0][0]
            interval_duration = delta if delta > timedelta(0) else infer_interval_resolution()
        else:
            interval_duration = infer_interval_resolution()

        if interval_duration <= timedelta(0):
            interval_duration = timedelta(minutes=15)

        # Calculate how many intervals we need for MIN_HOURS
        # Defensive check: ensure interval_duration is positive to prevent division by zero
        interval_seconds = interval_duration.total_seconds()
        if interval_seconds <= 0:
            _LOGGER.warning("Invalid interval duration detected, using 15-minute default")
            interval_seconds = AVERAGE_THRESHOLD_DEFAULT_INTERVAL_SECONDS
        intervals_needed = max(1, int(MIN_HOURS * 3600 / interval_seconds))

        # Pass 2: If we don't have enough future data, backfill with past
        if len(future_intervals) < intervals_needed:
            past_intervals_needed = intervals_needed - len(future_intervals)
            past_intervals = collect_past_intervals(past_intervals_needed)

            if len(past_intervals) >= past_intervals_needed:
                # Successfully backfilled to meet 24h minimum
                combined_intervals = past_intervals + future_intervals
                past_count = len(past_intervals)
                future_count = len(future_intervals)

                _LOGGER.debug(
                    "Average threshold: using %d past + %d future intervals (%.1fh total) to meet %dh minimum",
                    past_count, future_count,
                    (past_count + future_count) * interval_duration.total_seconds() / 3600,
                    MIN_HOURS
                )
            else:
                # Not enough past data available - use what we have
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
            # Enough future data
            combined_intervals = future_intervals
            past_count = 0
            future_count = len(future_intervals)

            _LOGGER.debug(
                "Average threshold: using %d future intervals (%.1fh total)",
                future_count, future_count * interval_duration.total_seconds() / 3600
            )

        # Calculate average from combined intervals
        if combined_intervals:
            prices = [p for _, p in combined_intervals]
            average = sum(prices) / len(prices)

            # Check if we have sufficient data (at least 24h worth)
            has_sufficient_data = len(combined_intervals) >= intervals_needed

            # Apply hysteresis: require N consecutive valid calculations before FIRST enabling
            # Once enabled, continue using as long as we have ANY data (graceful degradation)
            if has_sufficient_data:
                # We have sufficient data
                if not self._average_threshold_enabled:
                    # Not yet enabled - check hysteresis
                    self._average_threshold_valid_count += 1

                    if self._average_threshold_valid_count >= AVERAGE_THRESHOLD_HYSTERESIS_COUNT:
                        self._average_threshold_enabled = True
                        _LOGGER.info(
                            "Average threshold enabled after %d consecutive valid calculations: %.4f €/kWh",
                            self._average_threshold_valid_count, average
                        )
                    else:
                        _LOGGER.debug(
                            "Average threshold calculated (%.4f €/kWh) but not yet enabled "
                            "(%d/%d consecutive valid calculations)",
                            average, self._average_threshold_valid_count, AVERAGE_THRESHOLD_HYSTERESIS_COUNT
                        )
                else:
                    # Already enabled - keep using it
                    pass

                _LOGGER.debug(
                    "Calculated average threshold: %.4f €/kWh from %d intervals (enabled: %s, count: %d)",
                    average, len(combined_intervals), self._average_threshold_enabled,
                    self._average_threshold_valid_count
                )
                return round(average, 4)
            else:
                # Insufficient data for 24h minimum
                # If already enabled, allow graceful degradation (keep using with warning)
                # If not yet enabled, still return value but don't enable
                if self._average_threshold_enabled:
                    _LOGGER.warning(
                        "Average threshold: insufficient data for %dh minimum "
                        "(have %d intervals, need %d) - continuing with available data",
                        MIN_HOURS, len(combined_intervals), intervals_needed
                    )
                    return round(average, 4)
                else:
                    # Not enabled yet and insufficient data - reset counter but still return value
                    _LOGGER.debug(
                        "Average threshold: insufficient data for %dh minimum "
                        "(have %d intervals, need %d) - returning value without enabling",
                        MIN_HOURS, len(combined_intervals), intervals_needed
                    )
                    self._average_threshold_valid_count = 0
                    return round(average, 4)

        # No intervals available - reset
        if self._average_threshold_valid_count > 0 or self._average_threshold_enabled:
            _LOGGER.debug("Average threshold data unavailable - resetting hysteresis state")
        self._average_threshold_valid_count = 0
        self._average_threshold_enabled = False
        return None

    def _check_minimum_charging_window(
        self,
        prices_today: dict[str, Any] | None,
        prices_tomorrow: dict[str, Any] | None,
        transport_lookup: list[dict[str, Any]] | None,
        current_transport_cost: float | None,
        average_threshold: float | None = None,
        permissive_mode_active: bool = False,
        permissive_multiplier: float = DEFAULT_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
    ) -> bool:
        """Check if there are at least N consecutive hours of low prices starting from NOW.

        This is used for the OFF → ON transition: only allow car charging to start
        if the price will stay low for the configured duration (default 2 hours).
        When permissive mode is active, the low-price definition uses the
        permissive multiplier to extend the acceptable threshold.

        Returns True if all prices from NOW for the next N hours are below threshold.
        Returns False if any price in the next N hours exceeds threshold.
        """
        if not prices_today and not prices_tomorrow:
            return False

        now = dt_util.utcnow()

        # Get the threshold (either average or fixed)
        use_average = self.config.get(
            CONF_USE_AVERAGE_THRESHOLD, DEFAULT_USE_AVERAGE_THRESHOLD
        )

        if use_average and average_threshold is not None:
            base_threshold = average_threshold
        else:
            base_threshold = self.config.get(CONF_PRICE_THRESHOLD, DEFAULT_PRICE_THRESHOLD)

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

        timeline = self._build_price_timeline(
            prices_today,
            prices_tomorrow,
            transport_lookup,
            current_transport_cost,
            now,
        )

        if not timeline:
            return False

        self._last_price_timeline = timeline
        self._last_price_timeline_generated_at = now

        # Identify the interval that covers the current time
        current_idx: int | None = None
        for idx, (start_time, interval_end, _) in enumerate(timeline):
            if interval_end <= now:
                continue
            if start_time <= now < interval_end:
                current_idx = idx
                break

        if current_idx is None:
            _LOGGER.debug("No active Nord Pool interval covering current time %s", now.isoformat())
            return False

        min_duration_hours = self.config.get(
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

        # Extend the window with consecutive low-price intervals with no gaps
        previous_end = current_end
        for next_idx in range(current_idx + 1, len(timeline)):
            next_start, next_end, next_price = timeline[next_idx]

            # Any gap in coverage breaks the consecutive window requirement
            if next_start > previous_end + timedelta(seconds=PRICE_INTERVAL_GAP_TOLERANCE_SECONDS):
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

    def _calculate_forecast_summary(
        self,
        prices_today: dict[str, Any] | None,
        prices_tomorrow: dict[str, Any] | None,
        transport_lookup: list[dict[str, Any]] | None,
        current_transport_cost: float | None,
        minimum_average_threshold: float | None,
    ) -> dict[str, Any]:
        """Produce forecast insights (cheapest interval and best charging window)."""
        now = dt_util.utcnow()

        stale = False

        # Check if cached timeline is too old
        if (self._last_price_timeline_generated_at is not None and
            now - self._last_price_timeline_generated_at > self._price_timeline_max_age):
            _LOGGER.debug("Price timeline cache expired (age: %.1f hours), clearing",
                        (now - self._last_price_timeline_generated_at).total_seconds() / 3600)
            self._last_price_timeline = None
            self._last_price_timeline_generated_at = None

        if not prices_today and not prices_tomorrow:
            if self._last_price_timeline:
                timeline = self._last_price_timeline
                stale = True
            else:
                self._last_price_timeline = None
                self._last_price_timeline_generated_at = None
                return {"available": False}
        else:
            timeline = self._last_price_timeline or self._build_price_timeline(
                prices_today,
                prices_tomorrow,
                transport_lookup,
                current_transport_cost,
                now,
            )
            if timeline and self._last_price_timeline is None:
                self._last_price_timeline = timeline
                self._last_price_timeline_generated_at = now

        if not timeline:
            self._last_price_timeline = None
            self._last_price_timeline_generated_at = None
            return {"available": False}

        # Consider only intervals that are in the future
        future_segments = [(start, end, price) for start, end, price in timeline if end > now]
        if not future_segments:
            self._last_price_timeline = None
            self._last_price_timeline_generated_at = None
            return {"available": False}

        cheapest_segment = min(future_segments, key=lambda segment: segment[2])

        min_duration_hours = self.config.get(
            CONF_MIN_CAR_CHARGING_DURATION, DEFAULT_MIN_CAR_CHARGING_DURATION
        )
        required_duration = timedelta(hours=min_duration_hours)

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
                if (
                    best_window is None
                    or average_price < best_window["average_price"]
                ):
                    best_window = {
                        "start": window_start,
                        "end": current_time,
                        "average_price": average_price,
                    }

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
        if self._last_price_timeline_generated_at:
            summary["timeline_generated_at"] = _iso_local(self._last_price_timeline_generated_at)

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

    async def _get_transport_cost_lookup(
        self, current_transport_cost: float | None = None
    ) -> tuple[list[dict[str, Any]], str]:
        """Return cached transport cost lookup built from recorder history."""
        transport_entity = self.config.get(CONF_TRANSPORT_COST_ENTITY)
        if not transport_entity:
            self._transport_cost_lookup = []
            self._transport_cost_status = "not_configured"
            return [], "not_configured"

        now = dt_util.utcnow()
        # Refresh every 30 minutes at most
        if (
            self._transport_cost_lookup_time
            and now - self._transport_cost_lookup_time < timedelta(minutes=30)
        ):
            return self._transport_cost_lookup, self._transport_cost_status

        try:
            try:
                from homeassistant.components.recorder import get_instance as get_recorder_instance
            except ImportError:
                get_recorder_instance = None  # Older HA or test shims may not expose get_instance

            from homeassistant.components.recorder.history import get_significant_states

            end_time = dt_util.now()
            start_time = end_time - timedelta(days=7)

            if get_recorder_instance is not None:
                recorder = get_recorder_instance(self.hass)
                states = await recorder.async_add_executor_job(
                    get_significant_states,
                    self.hass,
                    start_time,
                    end_time,
                    [transport_entity],
                )
            else:
                # Fallback for environments without recorder.get_instance (tests/shims)
                states = await self.hass.async_add_executor_job(
                    get_significant_states,
                    self.hass,
                    start_time,
                    end_time,
                    [transport_entity],
                )

            if not states or transport_entity not in states:
                fallback_lookup = self._build_fallback_transport_lookup(
                    current_transport_cost
                )
                self._transport_cost_lookup = fallback_lookup
                if fallback_lookup:
                    self._transport_cost_status = "fallback_current"
                    self._maybe_log_transport_status(
                        "fallback_current",
                        "Using current transport cost value for all hours due to missing history on %s.",
                        transport_entity,
                    )
                else:
                    self._transport_cost_status = "pending_history"
                    self._maybe_log_transport_status(
                        "pending_history",
                        "No transport cost history available for %s. "
                        "Nord Pool prices will exclude transport cost until 7 days of history accumulate.",
                        transport_entity,
                    )
                self._transport_cost_lookup_time = now
                return self._transport_cost_lookup, self._transport_cost_status

            # First collect all valid cost changes with timestamps
            raw_changes: list[dict[str, Any]] = []
            for state in states[transport_entity]:
                value = state.state
                if value in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                    continue
                try:
                    cost = float(value)
                    timestamp = dt_util.as_utc(state.last_changed)
                    raw_changes.append(
                        {
                            "start": timestamp.isoformat(),
                            "cost": cost,
                        }
                    )
                except (ValueError, TypeError, AttributeError):
                    continue

            # Sort by timestamp first
            raw_changes.sort(key=lambda entry: entry["start"])

            # Then remove duplicate consecutive values
            changes: list[dict[str, Any]] = []
            last_cost: float | None = None
            for change in raw_changes:
                cost = change["cost"]
                if last_cost is None or abs(cost - last_cost) > 1e-9:
                    changes.append(change)
                    last_cost = cost

            self._transport_cost_lookup = changes
            self._transport_cost_status = "applied" if changes else "pending_history"
            self._transport_cost_lookup_time = now

            if self._transport_cost_status == "pending_history":
                self._maybe_log_transport_status(
                    "pending_history",
                    "Transport cost history for %s is incomplete. "
                    "Nord Pool prices will exclude transport cost until 7 days of history accumulate.",
                    transport_entity,
                )
            else:
                self._maybe_log_transport_status("applied", None)

            return self._transport_cost_lookup, self._transport_cost_status

        except Exception as err:
            fallback_lookup = self._build_fallback_transport_lookup(
                current_transport_cost
            )
            self._transport_cost_lookup = fallback_lookup
            if fallback_lookup:
                self._transport_cost_status = "fallback_current"
                self._maybe_log_transport_status(
                    "fallback_current",
                    "Using current transport cost value for all hours after history lookup failure on %s.",
                    transport_entity,
                )
            else:
                self._transport_cost_status = "error"
                self._maybe_log_transport_status(
                    "error",
                    "Failed to build transport cost lookup from history for %s: %s. "
                    "Nord Pool prices will exclude transport cost.",
                    transport_entity,
                    err,
                )
            self._transport_cost_lookup_time = now
            return self._transport_cost_lookup, self._transport_cost_status

    def _maybe_log_transport_status(self, status: str, message: str | None, *args) -> None:
        """Log transport cost status changes without spamming."""
        if status == self._transport_cost_last_log or message is None:
            self._transport_cost_last_log = status
            return

        _LOGGER.warning(message, *args)
        self._transport_cost_last_log = status

    def _build_fallback_transport_lookup(
        self, current_transport_cost: float | None
    ) -> list[dict[str, Any]]:
        """Build a lookup that uses the current transport cost for all hours."""
        if current_transport_cost is None:
            return []
        return [
            {
                "start": None,
                "cost": current_transport_cost,
            }
        ]

    async def _check_data_availability(self, data: dict[str, Any]) -> None:
        """Check data availability and send notifications if needed."""
        now = dt_util.utcnow()

        # Check if critical data is available
        data_is_available = self._is_data_available(data)

        if data_is_available:
            # Data is available - reset tracking
            self._last_successful_update = now
            if self._data_unavailable_since is not None:
                # Data was unavailable but is now available - send recovery notification
                unavailable_duration = now - self._data_unavailable_since
                await self._send_notification(
                    "Electricity Planner Data Restored",
                    f"Nord Pool data has been restored after {unavailable_duration.total_seconds():.0f} seconds. "
                    f"Charging decisions are now active.",
                    "electricity_planner_data_restored"
                )
                self._data_unavailable_since = None
                self._notification_sent = False
                _LOGGER.info("Data availability restored after %.1f seconds", unavailable_duration.total_seconds())
        else:
            # Data is not available
            if self._data_unavailable_since is None:
                # First time detecting unavailability
                self._data_unavailable_since = now
                _LOGGER.warning("Critical data unavailable - starting tracking")
            else:
                # Data has been unavailable for some time
                unavailable_duration = now - self._data_unavailable_since

                # Send notification if data unavailable for more than 1 minute and notification not sent yet
                if unavailable_duration > timedelta(minutes=1) and not self._notification_sent:
                    await self._send_notification(
                        "Electricity Planner Data Unavailable",
                        f"Critical data (Nord Pool prices) has been unavailable for {unavailable_duration.total_seconds():.0f} seconds. "
                        f"All charging from grid is disabled for safety. Please check your Nord Pool integration.",
                        "electricity_planner_data_unavailable"
                    )
                    self._notification_sent = True
                    _LOGGER.error("Data unavailable notification sent after %.1f seconds", unavailable_duration.total_seconds())

    async def _send_notification(self, title: str, message: str, notification_id: str) -> None:
        """Send a persistent notification."""
        try:
            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": title,
                    "message": message,
                    "notification_id": notification_id,
                },
            )
            _LOGGER.info("Sent notification: %s", title)
        except Exception as err:
            _LOGGER.error("Failed to send notification: %s", err)

    def is_data_available(self) -> bool:
        """Public helper for consumers needing availability."""
        if not self.data:
            return False
        return self._is_data_available(self.data)

    @property
    def last_successful_update(self) -> Optional[datetime]:
        """Expose last successful update timestamp."""
        return self._last_successful_update

    @property
    def data_unavailable_since(self) -> Optional[datetime]:
        """Expose when data became unavailable."""
        return self._data_unavailable_since

    @property
    def notification_sent(self) -> bool:
        """Expose notification flag."""
        return self._notification_sent

    @property
    def min_soc_threshold(self) -> float:
        """Get minimum SOC threshold."""
        return self.config.get(CONF_MIN_SOC_THRESHOLD, DEFAULT_MIN_SOC)

    @property
    def max_soc_threshold(self) -> float:
        """Get maximum SOC threshold."""
        return self.config.get(CONF_MAX_SOC_THRESHOLD, DEFAULT_MAX_SOC)

    @property
    def price_threshold(self) -> float:
        """Get price threshold."""
        return self.config.get(CONF_PRICE_THRESHOLD, DEFAULT_PRICE_THRESHOLD)
