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
    CONF_SOLAR_FORECAST_CURRENT_ENTITY,
    CONF_SOLAR_FORECAST_NEXT_ENTITY,
    CONF_SOLAR_FORECAST_TODAY_ENTITY,
    CONF_SOLAR_FORECAST_REMAINING_TODAY_ENTITY,
    CONF_SOLAR_FORECAST_TOMORROW_ENTITY,
    CONF_TRANSPORT_COST_ENTITY,
    CONF_MIN_SOC_THRESHOLD,
    CONF_MAX_SOC_THRESHOLD,
    CONF_PRICE_THRESHOLD,
    CONF_PRICE_ADJUSTMENT_MULTIPLIER,
    CONF_PRICE_ADJUSTMENT_OFFSET,
    CONF_USE_AVERAGE_THRESHOLD,
    DEFAULT_MIN_SOC,
    DEFAULT_MAX_SOC,
    DEFAULT_PRICE_THRESHOLD,
    DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER,
    DEFAULT_PRICE_ADJUSTMENT_OFFSET,
    DEFAULT_USE_AVERAGE_THRESHOLD,
    DEFAULT_MIN_CAR_CHARGING_DURATION_HOURS,
)
from .decision_engine import ChargingDecisionEngine

_LOGGER = logging.getLogger(__name__)


class ElectricityPlannerCoordinator(DataUpdateCoordinator):
    """Coordinator for electricity planner data."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.hass = hass
        self.entry = entry
        self.config = entry.data

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
        self._transport_cost_lookup: dict[int, float] = {}
        self._transport_cost_lookup_time: datetime | None = None
        self._transport_cost_status: str = "not_configured"
        self._transport_cost_last_log: str | None = None

        # Car charging state tracking for hysteresis
        self._previous_car_charging: bool = False

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

        # Solar forecast entities
        for entity_key in [CONF_SOLAR_FORECAST_CURRENT_ENTITY, CONF_SOLAR_FORECAST_NEXT_ENTITY,
                          CONF_SOLAR_FORECAST_TODAY_ENTITY, CONF_SOLAR_FORECAST_REMAINING_TODAY_ENTITY,
                          CONF_SOLAR_FORECAST_TOMORROW_ENTITY]:
            if self.config.get(entity_key):
                entities_to_track.append(self.config[entity_key])

        if entities_to_track:
            async_track_state_change_event(
                self.hass, entities_to_track, self._handle_entity_change
            )

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


        # Solar forecast data
        data["solar_forecast_current"] = await self._get_state_value(
            self.config.get(CONF_SOLAR_FORECAST_CURRENT_ENTITY)
        )
        data["solar_forecast_next"] = await self._get_state_value(
            self.config.get(CONF_SOLAR_FORECAST_NEXT_ENTITY)  
        )
        data["solar_forecast_today"] = await self._get_state_value(
            self.config.get(CONF_SOLAR_FORECAST_TODAY_ENTITY)
        )
        data["solar_forecast_remaining_today"] = await self._get_state_value(
            self.config.get(CONF_SOLAR_FORECAST_REMAINING_TODAY_ENTITY)
        )
        data["solar_forecast_tomorrow"] = await self._get_state_value(
            self.config.get(CONF_SOLAR_FORECAST_TOMORROW_ENTITY)
        )

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

        transport_lookup, transport_status = await self._get_transport_cost_lookup()
        data["transport_cost_lookup"] = transport_lookup
        data["transport_cost_status"] = transport_status

        # Calculate average threshold if enabled
        data["average_threshold"] = self._calculate_average_threshold(
            data.get("nordpool_prices_today"),
            data.get("nordpool_prices_tomorrow"),
            transport_lookup
        )

        # Calculate if we have at least 2 hours of low prices ahead for car charging
        data["has_min_charging_window"] = self._check_minimum_charging_window(
            data.get("nordpool_prices_today"),
            data.get("nordpool_prices_tomorrow"),
            transport_lookup
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
        transport_lookup: dict[int, float] | None
    ) -> float | None:
        """Calculate average of all future prices (from now onwards).

        This provides a dynamic threshold that adapts to the available price data.
        Prices include full adjustments (multiplier + offset + transport cost).

        Returns None if no future prices are available.
        """
        if not prices_today and not prices_tomorrow:
            return None

        from datetime import datetime
        now = dt_util.now()
        future_prices: list[float] = []

        # Get multiplier and offset for price adjustments
        multiplier = self.config.get(
            CONF_PRICE_ADJUSTMENT_MULTIPLIER, DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER
        )
        offset = self.config.get(
            CONF_PRICE_ADJUSTMENT_OFFSET, DEFAULT_PRICE_ADJUSTMENT_OFFSET
        )

        # Helper to process intervals
        def process_intervals(intervals: list[dict[str, Any]]) -> None:
            for interval in intervals:
                try:
                    # Check if interval is in the future
                    start_time_str = interval.get("start")
                    if not start_time_str:
                        continue

                    start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
                    if start_time <= now:
                        continue  # Skip past intervals

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
                    transport_cost = 0.0
                    if transport_lookup:
                        hour = start_time.hour
                        transport_cost = transport_lookup.get(hour, 0.0)

                    final_price = adjusted_price + transport_cost
                    future_prices.append(final_price)

                except Exception as err:
                    _LOGGER.debug("Error processing interval for average threshold: %s", err)
                    continue

        # Process today's prices
        if prices_today:
            area_code = next(iter(prices_today.keys()), None)
            if area_code and isinstance(prices_today[area_code], list):
                process_intervals(prices_today[area_code])

        # Process tomorrow's prices
        if prices_tomorrow:
            area_code = next(iter(prices_tomorrow.keys()), None)
            if area_code and isinstance(prices_tomorrow[area_code], list):
                process_intervals(prices_tomorrow[area_code])

        # Calculate average
        if future_prices:
            average = sum(future_prices) / len(future_prices)
            _LOGGER.debug(
                "Calculated average threshold from %d future prices: %.4f €/kWh",
                len(future_prices), average
            )
            return round(average, 4)

        return None

    def _check_minimum_charging_window(
        self,
        prices_today: dict[str, Any] | None,
        prices_tomorrow: dict[str, Any] | None,
        transport_lookup: dict[int, float] | None
    ) -> bool:
        """Check if there are at least 2 consecutive hours of low prices starting now.

        Returns True if the current price and the next 2 hours are all below threshold.
        This prevents car charging from starting for very short low-price periods.
        """
        if not prices_today and not prices_tomorrow:
            return False

        from datetime import datetime
        now = dt_util.now()

        # Get the threshold (either average or fixed)
        use_average = self.config.get(CONF_USE_AVERAGE_THRESHOLD, DEFAULT_USE_AVERAGE_THRESHOLD)
        average_threshold = self._calculate_average_threshold(prices_today, prices_tomorrow, transport_lookup)

        if use_average and average_threshold is not None:
            threshold = average_threshold
        else:
            threshold = self.config.get(CONF_PRICE_THRESHOLD, DEFAULT_PRICE_THRESHOLD)

        # Get multiplier and offset for price adjustments
        multiplier = self.config.get(
            CONF_PRICE_ADJUSTMENT_MULTIPLIER, DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER
        )
        offset = self.config.get(
            CONF_PRICE_ADJUSTMENT_OFFSET, DEFAULT_PRICE_ADJUSTMENT_OFFSET
        )

        # Collect all future intervals with their prices
        future_intervals: list[tuple[datetime, float]] = []

        def process_intervals(intervals: list[dict[str, Any]]) -> None:
            for interval in intervals:
                try:
                    start_time_str = interval.get("start")
                    if not start_time_str:
                        continue

                    start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
                    if start_time < now:
                        continue  # Skip past intervals

                    # Extract and convert price
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

                    # Add transport cost
                    transport_cost = 0.0
                    if transport_lookup:
                        hour = start_time.hour
                        transport_cost = transport_lookup.get(hour, 0.0)

                    final_price = adjusted_price + transport_cost
                    future_intervals.append((start_time, final_price))

                except Exception as err:
                    _LOGGER.debug("Error processing interval for charging window check: %s", err)
                    continue

        # Process today's and tomorrow's prices
        if prices_today:
            area_code = next(iter(prices_today.keys()), None)
            if area_code and isinstance(prices_today[area_code], list):
                process_intervals(prices_today[area_code])

        if prices_tomorrow:
            area_code = next(iter(prices_tomorrow.keys()), None)
            if area_code and isinstance(prices_tomorrow[area_code], list):
                process_intervals(prices_tomorrow[area_code])

        # Sort by time
        future_intervals.sort(key=lambda x: x[0])

        if not future_intervals:
            return False

        # Check if we have at least 2 hours of consecutive low prices
        # We need the current interval + at least 2 more hours (depends on Nord Pool interval duration)
        min_duration_hours = DEFAULT_MIN_CAR_CHARGING_DURATION_HOURS
        current_time = now
        low_price_duration = timedelta(hours=0)

        for start_time, price in future_intervals:
            if price > threshold:
                # Hit a high price, reset counter
                low_price_duration = timedelta(hours=0)
                current_time = start_time
                continue

            # Check if this interval is continuous with our window
            if low_price_duration == timedelta(hours=0):
                # Starting a new window
                current_time = start_time

            # Calculate interval duration (look at next interval or assume standard duration)
            idx = future_intervals.index((start_time, price))
            if idx + 1 < len(future_intervals):
                next_start = future_intervals[idx + 1][0]
                interval_duration = next_start - start_time
            else:
                # Assume hourly intervals if we don't have next interval
                interval_duration = timedelta(hours=1)

            low_price_duration += interval_duration

            # Check if we've accumulated enough low-price time
            if low_price_duration >= timedelta(hours=min_duration_hours):
                _LOGGER.debug(
                    "Found minimum charging window: %.1f hours of prices below %.4f €/kWh",
                    low_price_duration.total_seconds() / 3600, threshold
                )
                return True

        _LOGGER.debug(
            "No minimum charging window found: only %.1f hours of low prices (need %d)",
            low_price_duration.total_seconds() / 3600, min_duration_hours
        )
        return False

    async def _get_transport_cost_lookup(self) -> tuple[dict[int, float], str]:
        """Return cached transport cost lookup built from recorder history."""
        transport_entity = self.config.get(CONF_TRANSPORT_COST_ENTITY)
        if not transport_entity:
            self._transport_cost_lookup = {}
            self._transport_cost_status = "not_configured"
            return {}, "not_configured"

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
                self._transport_cost_lookup = {}
                self._transport_cost_status = "pending_history"
                self._maybe_log_transport_status(
                    "pending_history",
                    "No transport cost history available for %s. "
                    "Nord Pool prices will exclude transport cost until 7 days of history accumulate.",
                    transport_entity,
                )
                self._transport_cost_lookup_time = now
                return self._transport_cost_lookup, self._transport_cost_status

            hour_costs: dict[int, list[float]] = {hour: [] for hour in range(24)}
            for state in states[transport_entity]:
                value = state.state
                if value in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                    continue
                try:
                    cost = float(value)
                    hour = state.last_changed.hour
                    hour_costs[hour].append(cost)
                except (ValueError, TypeError, AttributeError):
                    continue

            lookup: dict[int, float] = {}
            for hour, values in hour_costs.items():
                if values:
                    lookup[hour] = values[-1]

            # Fill gaps with neighboring hours
            for hour in range(24):
                if hour not in lookup:
                    next_hour = (hour + 1) % 24
                    prev_hour = (hour - 1) % 24
                    if next_hour in lookup:
                        lookup[hour] = lookup[next_hour]
                    elif prev_hour in lookup:
                        lookup[hour] = lookup[prev_hour]

            self._transport_cost_lookup = lookup
            self._transport_cost_status = "applied" if lookup else "pending_history"
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
            self._transport_cost_lookup = {}
            self._transport_cost_status = "error"
            self._transport_cost_lookup_time = now
            self._maybe_log_transport_status(
                "error",
                "Failed to build transport cost lookup from history for %s: %s. "
                "Nord Pool prices will exclude transport cost.",
                transport_entity,
                err,
            )
            return self._transport_cost_lookup, self._transport_cost_status

    def _maybe_log_transport_status(self, status: str, message: str | None, *args) -> None:
        """Log transport cost status changes without spamming."""
        if status == self._transport_cost_last_log or message is None:
            self._transport_cost_last_log = status
            return

        _LOGGER.warning(message, *args)
        self._transport_cost_last_log = status

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
