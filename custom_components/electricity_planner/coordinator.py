"""Data coordinator for Electricity Planner."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    CONF_NORDPOOL_CONFIG_ENTRY,
    CONF_CURRENT_PRICE_ENTITY,
    CONF_HIGHEST_PRICE_ENTITY,
    CONF_LOWEST_PRICE_ENTITY,
    CONF_NEXT_PRICE_ENTITY,
    CONF_BATTERY_SOC_ENTITIES,
    CONF_SOLAR_PRODUCTION_ENTITY,
    CONF_HOUSE_CONSUMPTION_ENTITY,
    CONF_CAR_CHARGING_POWER_ENTITY,
    CONF_MONTHLY_GRID_PEAK_ENTITY,
    CONF_TRANSPORT_COST_ENTITY,
    CONF_MIN_SOC_THRESHOLD,
    CONF_MAX_SOC_THRESHOLD,
    CONF_PRICE_THRESHOLD,
    CONF_PRICE_ADJUSTMENT_MULTIPLIER,
    CONF_PRICE_ADJUSTMENT_OFFSET,
    CONF_USE_AVERAGE_THRESHOLD,
    CONF_MIN_CAR_CHARGING_DURATION,
    DEFAULT_MIN_SOC,
    DEFAULT_MAX_SOC,
    DEFAULT_PRICE_THRESHOLD,
    DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER,
    DEFAULT_PRICE_ADJUSTMENT_OFFSET,
    DEFAULT_USE_AVERAGE_THRESHOLD,
    DEFAULT_MIN_CAR_CHARGING_DURATION,
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

        self.decision_engine = ChargingDecisionEngine(hass, self.config)

        # Data availability tracking
        self._last_successful_update = dt_util.utcnow()
        self._data_unavailable_since = None
        self._notification_sent = False

        # Update throttling
        self._last_entity_update = None
        self._min_update_interval = timedelta(seconds=10)  # Minimum 10s between entity-triggered updates

        # Nord Pool price caching (prices only update hourly)
        self._nordpool_cache = {}
        self._nordpool_cache_time = {}

        # Transport cost lookup caching (expensive recorder query)
        self._transport_cost_lookup: list[dict[str, Any]] = []
        self._transport_cost_lookup_time: datetime | None = None
        self._transport_cost_status: str = "not_configured"
        self._transport_cost_last_log: str | None = None

        # Car charging state tracking for hysteresis
        self._previous_car_charging: bool = False

        # Manual override tracking
        self._manual_overrides: dict[str, dict[str, Any] | None] = {
            "battery_grid_charging": None,
            "car_grid_charging": None,
        }
        self._last_price_timeline: list[tuple[datetime, datetime, float]] | None = None

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
        for entity_key in [CONF_SOLAR_PRODUCTION_ENTITY, CONF_HOUSE_CONSUMPTION_ENTITY, CONF_CAR_CHARGING_POWER_ENTITY, CONF_MONTHLY_GRID_PEAK_ENTITY]:
            if self.config.get(entity_key):
                entities_to_track.append(self.config[entity_key])


        if entities_to_track:
            async_track_state_change_event(
                self.hass, entities_to_track, self._handle_entity_change
            )

    def _resolve_transport_cost(
        self,
        transport_lookup: list[dict[str, Any]] | None,
        start_time_utc: datetime,
        reference_now: datetime | None = None,
    ) -> float:
        """Resolve transport cost for a specific timestamp."""
        if not transport_lookup:
            return 0.0

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

        return cost if cost is not None else 0.0

    def _resolve_override_targets(self, target: str) -> tuple[str, ...]:
        """Resolve override target string into coordinator keys."""
        mapping = {
            "battery": ("battery_grid_charging",),
            "car": ("car_grid_charging",),
            "both": ("battery_grid_charging", "car_grid_charging"),
        }
        return mapping.get(target, ())

    async def async_set_manual_override(
        self,
        target: str,
        value: bool,
        duration: timedelta | None,
        reason: str | None,
    ) -> None:
        """Apply a manual override for battery or car decisions."""
        now = dt_util.utcnow()
        expires_at = now + duration if duration is not None else None
        manual_reason = reason or ("force charge" if value else "force wait")

        for coordinator_key in self._resolve_override_targets(target):
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

    async def async_clear_manual_override(self, target: str | None = None) -> None:
        """Clear manual overrides for the given target (or all)."""
        effective_target = target or "both"
        for coordinator_key in self._resolve_override_targets(effective_target):
            if self._manual_overrides.get(coordinator_key):
                _LOGGER.info("Manual override cleared for %s", coordinator_key)
            self._manual_overrides[coordinator_key] = None

    def _apply_manual_overrides(self, decision: dict[str, Any]) -> dict[str, Any]:
        """Apply active manual overrides to the decision payload."""
        now = dt_util.utcnow()
        overrides_info: dict[str, Any] = {}
        base_trace = decision.get("strategy_trace") or []
        augmented_trace = list(base_trace)

        for coordinator_key, override in self._manual_overrides.items():
            if not override:
                continue

            expires_at: datetime | None = override.get("expires_at")
            if expires_at and expires_at <= now:
                self._manual_overrides[coordinator_key] = None
                continue

            manual_reason: str = override.get("reason") or (
                "Manual override to charge" if override.get("value") else "Manual override to wait"
            )
            decision[coordinator_key] = override["value"]

            reason_key = f"{coordinator_key}_reason"
            existing_reason = decision.get(reason_key)
            if existing_reason:
                decision[reason_key] = f"{existing_reason} (override: {manual_reason})"
            else:
                decision[reason_key] = f"Manual override: {manual_reason}"

            overrides_info[coordinator_key] = {
                "value": override["value"],
                "reason": manual_reason,
                "set_at": override.get("set_at").isoformat() if override.get("set_at") else None,
                "expires_at": expires_at.isoformat() if expires_at else None,
            }

            augmented_trace.append(
                {
                    "strategy": "ManualOverride",
                    "priority": -1,
                    "should_charge": override["value"],
                    "reason": manual_reason,
                    "target": coordinator_key,
                }
            )

        if overrides_info:
            decision["manual_overrides"] = overrides_info
            decision["strategy_trace"] = augmented_trace
        else:
            decision.setdefault("manual_overrides", {})

        return decision

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

                    end_time = None
                    end_time_str = interval.get("end")
                    if end_time_str:
                        try:
                            end_time = dt_util.parse_datetime(end_time_str)
                            if end_time <= start_time:
                                end_time = None
                        except Exception:
                            end_time = None

                    # Skip intervals that have completely ended
                    if end_time is not None:
                        if end_time <= now:
                            continue
                    else:
                        # Assume hourly interval if no end provided
                        if start_time_utc < now - timedelta(hours=1):
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

                    price_kwh = price_value / 1000
                    adjusted_price = (price_kwh * multiplier) + offset

                    transport_cost = self._resolve_transport_cost(
                        transport_lookup, start_time_utc, reference_now=now
                    )
                    if transport_cost == 0.0 and current_transport_cost is not None:
                        transport_cost = current_transport_cost

                    final_price = adjusted_price + transport_cost
                    future_intervals.append((start_time_utc, end_time, final_price))

                except Exception as err:
                    _LOGGER.debug(
                        "Error processing interval for price timeline: %s", err
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
            estimated_resolution = timedelta(minutes=15)

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
    def _handle_entity_change(self, event):
        """Handle entity state changes with minimum update interval."""
        entity_id = event.data.get("entity_id")
        _LOGGER.debug("Entity changed: %s", entity_id)

        # Trigger immediate updates for critical entities
        critical_entities = [
            self.config.get(CONF_CURRENT_PRICE_ENTITY),
            self.config.get(CONF_SOLAR_PRODUCTION_ENTITY),
            self.config.get(CONF_HOUSE_CONSUMPTION_ENTITY),
        ]

        # Trigger updates for battery SOC changes (any configured battery)
        battery_entities = self.config.get(CONF_BATTERY_SOC_ENTITIES, [])

        if entity_id in critical_entities or entity_id in battery_entities:
            now = dt_util.utcnow()

            # Apply minimum interval throttling
            if (self._last_entity_update is None or
                now - self._last_entity_update >= self._min_update_interval):

                self._last_entity_update = now
                self.async_create_task(self.async_request_refresh())
                _LOGGER.debug("Entity update triggered for %s (throttled to %ds minimum)",
                            entity_id, self._min_update_interval.total_seconds())
            else:
                time_remaining = (self._last_entity_update + self._min_update_interval - now).total_seconds()
                _LOGGER.debug("Entity update skipped for %s (throttled, %.1fs remaining)",
                            entity_id, time_remaining)

    async def _async_update_data(self) -> dict[str, Any]:
        """Update data via library."""
        try:
            data = await self._fetch_all_data()

            # Add previous car charging state for hysteresis logic
            data["previous_car_charging"] = self._previous_car_charging

            charging_decision = await self.decision_engine.evaluate_charging_decision(data)
            charging_decision = self._apply_manual_overrides(charging_decision)

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
                battery_soc_values.append({"entity_id": entity_id, "soc": soc})
            else:
                _LOGGER.warning("Battery entity %s is unavailable - excluding from calculations", entity_id)

        data["battery_soc"] = battery_soc_values
        _LOGGER.debug("Final battery SOC data: %s", battery_soc_values)

        # Power data
        solar_production = await self._get_state_value(
            self.config.get(CONF_SOLAR_PRODUCTION_ENTITY)
        )
        house_consumption = await self._get_state_value(
            self.config.get(CONF_HOUSE_CONSUMPTION_ENTITY)
        )
        
        data["solar_production"] = solar_production
        data["house_consumption"] = house_consumption
        
        # Calculate solar surplus dynamically (production - consumption, cannot be negative)
        if solar_production is not None and house_consumption is not None:
            solar_surplus = max(0, solar_production - house_consumption)
        elif solar_production is not None:
            solar_surplus = solar_production  # Assume minimal consumption if not available
        else:
            solar_surplus = 0  # No solar data available
            
        data["solar_surplus"] = solar_surplus

        _LOGGER.debug("Solar production: %sW, house consumption: %sW, available surplus: %sW (for batteries/car/export)", 
                     solar_production, house_consumption, solar_surplus)

        data["car_charging_power"] = await self._get_state_value(
            self.config.get(CONF_CAR_CHARGING_POWER_ENTITY)
        )

        data["monthly_grid_peak"] = await self._get_state_value(
            self.config.get(CONF_MONTHLY_GRID_PEAK_ENTITY)
        )

        # Preserve car charging locked threshold across updates (for threshold continuity)
        data["car_charging_locked_threshold"] = self.data.get("car_charging_locked_threshold") if self.data else None



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

        # Calculate if we have at least 2 hours of low prices ahead for car charging
        data["has_min_charging_window"] = self._check_minimum_charging_window(
            data.get("nordpool_prices_today"),
            data.get("nordpool_prices_tomorrow"),
            transport_lookup,
            data.get("transport_cost"),
            average_threshold,
        )

        data["forecast_summary"] = self._calculate_forecast_summary(
            data.get("nordpool_prices_today"),
            data.get("nordpool_prices_tomorrow"),
            transport_lookup,
            data.get("transport_cost"),
            average_threshold,
        )

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
            _LOGGER.warning("Could not convert state to float: %s = %s", entity_id, state.state)
            return None

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

        if cache_key in self._nordpool_cache:
            cache_age = now - self._nordpool_cache_time.get(cache_key, now)
            # Cache for 5 minutes (prices change hourly, tomorrow appears at 13:00)
            if cache_age < timedelta(minutes=5):
                _LOGGER.debug("Using cached Nord Pool prices for %s (age: %.1f minutes)",
                            day, cache_age.total_seconds() / 60)
                return self._nordpool_cache[cache_key]

        try:
            # Calculate the target date
            now = dt_util.now()
            if day == "today":
                target_date = now.date()
            elif day == "tomorrow":
                target_date = (now + timedelta(days=1)).date()
            else:
                _LOGGER.error("Invalid day parameter: %s (expected 'today' or 'tomorrow')", day)
                return None

            # Call the Nord Pool service
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

            # The service returns a dict with area-based price data
            if response and isinstance(response, dict):
                # Count total entries across all areas for logging
                total_entries = sum(len(v) for v in response.values() if isinstance(v, list))
                _LOGGER.debug("Fetched Nord Pool prices for %s (%s): %d entries across %d area(s)",
                            day, target_date.isoformat(), total_entries, len(response))

                # Cache the response
                self._nordpool_cache[cache_key] = response
                self._nordpool_cache_time[cache_key] = dt_util.utcnow()

                return response
            else:
                _LOGGER.warning("Nord Pool service returned unexpected format for %s", day)
                return None

        except Exception as err:
            _LOGGER.warning("Failed to fetch Nord Pool prices for %s: %s", day, err)
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

                    final_price = adjusted_price + transport_cost
                    all_intervals.append((start_time_utc, final_price))

                except Exception as err:
                    _LOGGER.debug("Error processing interval for average threshold: %s", err)
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
        intervals_needed = max(1, int(MIN_HOURS * 3600 / interval_duration.total_seconds()))

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
            _LOGGER.debug(
                "Calculated average threshold: %.4f €/kWh from %d intervals",
                average, len(combined_intervals)
            )
            return round(average, 4)

        return None

    def _check_minimum_charging_window(
        self,
        prices_today: dict[str, Any] | None,
        prices_tomorrow: dict[str, Any] | None,
        transport_lookup: list[dict[str, Any]] | None,
        current_transport_cost: float | None,
        average_threshold: float | None = None,
    ) -> bool:
        """Check if there are at least N consecutive hours of low prices starting from NOW.

        This is used for the OFF → ON transition: only allow car charging to start
        if the price will stay low for the configured duration (default 2 hours).

        Returns True if all prices from NOW for the next N hours are below threshold.
        Returns False if any price in the next N hours exceeds threshold.
        """
        if not prices_today and not prices_tomorrow:
            self._last_price_timeline = None
            return False

        now = dt_util.utcnow()

        # Get the threshold (either average or fixed)
        use_average = self.config.get(
            CONF_USE_AVERAGE_THRESHOLD, DEFAULT_USE_AVERAGE_THRESHOLD
        )

        if use_average and average_threshold is not None:
            threshold = average_threshold
        else:
            threshold = self.config.get(CONF_PRICE_THRESHOLD, DEFAULT_PRICE_THRESHOLD)

        timeline = self._build_price_timeline(
            prices_today,
            prices_tomorrow,
            transport_lookup,
            current_transport_cost,
            now,
        )

        if not timeline:
            self._last_price_timeline = None
            return False

        self._last_price_timeline = timeline

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
        if current_price > threshold:
            _LOGGER.debug(
                "Current price %.4f €/kWh exceeds threshold %.4f €/kWh - no charging window starting now",
                current_price,
                threshold,
            )
            return False

        low_price_duration = current_end - max(now, current_start)
        if low_price_duration >= required_duration:
            _LOGGER.debug(
                "Found %d-hour charging window entirely within current interval (price %.4f €/kWh ≤ %.4f €/kWh)",
                min_duration_hours,
                current_price,
                threshold,
            )
            return True

        # Extend the window with consecutive low-price intervals with no gaps
        previous_end = current_end
        for next_idx in range(current_idx + 1, len(timeline)):
            next_start, next_end, next_price = timeline[next_idx]

            # Any gap in coverage breaks the consecutive window requirement
            if next_start > previous_end + timedelta(seconds=5):
                _LOGGER.debug(
                    "Charging window broken by gap between %s and %s",
                    previous_end.isoformat(),
                    next_start.isoformat(),
                )
                break

            if next_price > threshold:
                _LOGGER.debug(
                    "Charging window broken by high price %.4f €/kWh (>%.4f €/kWh)",
                    next_price,
                    threshold,
                )
                break

            effective_start = max(previous_end, next_start)
            if next_end <= effective_start:
                continue

            low_price_duration += next_end - effective_start
            previous_end = max(previous_end, next_end)

            if low_price_duration >= required_duration:
                _LOGGER.debug(
                    "Found %d-hour charging window: %.1f hours of low prices (≤%.4f €/kWh) ahead",
                    min_duration_hours,
                    low_price_duration.total_seconds() / 3600,
                    threshold,
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

        if not prices_today and not prices_tomorrow:
            self._last_price_timeline = None
            return {"available": False}

        timeline = self._last_price_timeline or self._build_price_timeline(
            prices_today,
            prices_tomorrow,
            transport_lookup,
            current_transport_cost,
            now,
        )

        if not timeline:
            self._last_price_timeline = None
            return {"available": False}

        # Consider only intervals that are in the future
        future_segments = [(start, end, price) for start, end, price in timeline if end > now]
        if not future_segments:
            self._last_price_timeline = None
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

        summary: dict[str, Any] = {
            "available": True,
            "cheapest_interval_start": cheapest_segment[0].isoformat(),
            "cheapest_interval_end": cheapest_segment[1].isoformat(),
            "cheapest_interval_price": round(cheapest_segment[2], 4),
            "average_threshold": minimum_average_threshold,
            "evaluated_at": now.isoformat(),
        }

        if best_window:
            summary.update(
                {
                    "best_window_hours": min_duration_hours,
                    "best_window_start": best_window["start"].isoformat(),
                    "best_window_end": best_window["end"].isoformat(),
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
            from homeassistant.components.recorder.history import get_significant_states

            end_time = dt_util.now()
            start_time = end_time - timedelta(days=7)

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
