"""Data coordinator for Electricity Planner."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
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
    CONF_PHASE_GRID_POWER_ENTITY,
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
    CONF_P1_TARIFF_ENTITY,
    CONF_SOLAR_FORECAST_ENTITY_TOMORROW,
    CONF_BASE_GRID_SETPOINT,
    CONF_MIN_SOC_THRESHOLD,

    CONF_MAX_SOC_THRESHOLD,
    CONF_PRICE_THRESHOLD,
    CONF_PRICE_ADJUSTMENT_MULTIPLIER,
    CONF_PRICE_ADJUSTMENT_OFFSET,
    CONF_FEEDIN_ADJUSTMENT_MULTIPLIER,
    CONF_FEEDIN_ADJUSTMENT_OFFSET,
    CONF_USE_AVERAGE_THRESHOLD,
    CONF_MIN_CAR_CHARGING_THRESHOLD,
    CONF_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
    DEFAULT_MIN_SOC,

    DEFAULT_MAX_SOC,
    DEFAULT_PRICE_THRESHOLD,
    DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER,
    DEFAULT_PRICE_ADJUSTMENT_OFFSET,
    DEFAULT_FEEDIN_ADJUSTMENT_MULTIPLIER,
    DEFAULT_FEEDIN_ADJUSTMENT_OFFSET,
    DEFAULT_USE_AVERAGE_THRESHOLD,
    DEFAULT_MIN_CAR_CHARGING_THRESHOLD,
    DEFAULT_BASE_GRID_SETPOINT,
    DEFAULT_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
    NORDPOOL_CACHE_MAX_SIZE,
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
from .arbitrage_mode import ArbitrageModePlanner
from .negative_buy import NegativeBuyPlanner
from .charging_window import ChargingWindowValidator
from .entity_status import EntityStatusReporter
from .forecast_summary import ForecastSummaryCalculator
from .manual_overrides import ManualOverrideManager
from .nordpool_service import NordpoolService
from .price_timeline import PriceTimelineBuilder
from .runtime_modes import RuntimeModeManager
from .solar_forecast import SolarForecastService
from .threshold_calculator import ThresholdCalculator
from .transport_cost import TransportCostResolver
from .helpers import (
    PriceInterval,
    apply_price_adjustment,
    extract_price_from_interval,
    is_in_month_peak_transition_window,
    parse_datetime_cached,
)

_LOGGER = logging.getLogger(__name__)
_MANUAL_OVERRIDE_STORE_VERSION = 1
_CAR_PERMISSIVE_MODE_STORE_VERSION = 1
_RUNTIME_MODE_STORE_VERSION = 1
_BATTERY_CHARGING_STATE_STORE_VERSION = 1


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

        # Entity listener unsubscribe callback (set in _setup_entity_tracking)
        self._entity_unsub: callback | None = None
        self._tracked_entity_ids: set[str] = set()

        # Permissive mode state (controlled via switch entity, persisted across updates)
        self._car_permissive_mode_active: bool = False
        self._car_permissive_mode_has_persisted_state: bool = False

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
        self._previous_battery_grid_charging: bool = False
        self._battery_grid_charging_changed_at: datetime | None = None
        self._battery_grid_charging_locked_threshold: float | None = None
        self._battery_charging_state_store: Store[dict[str, Any]] | None = None

        # Price interval tracking for threshold stability
        self._current_price_interval_start: datetime | None = None
        self._battery_threshold_snapshot: float | None = None
        self._last_config_hash: int | None = None  # Track config changes for threshold updates

        # Runtime mode and manual override tracking
        self._car_permissive_mode_store: Store[dict[str, Any]] | None = None
        self._runtime_mode_store: Store[dict[str, Any]] | None = None
        self._arbitrage_mode_state: dict[str, Any] | None = None
        self._negative_buy_mode_state: dict[str, Any] | None = None
        self._manual_overrides: dict[str, dict[str, Any] | None] = {
            "battery_grid_charging": None,
            "car_grid_charging": None,
            "charger_limit": None,
            "grid_setpoint": None,
        }
        self._manual_override_store: Store[dict[str, Any]] | None = None
        self._last_price_timeline: list[PriceInterval] | None = None
        self._last_price_timeline_generated_at: datetime | None = None
        self._last_price_timeline_data_hash: str | None = None  # Hash of price data used to build timeline
        self._price_timeline_max_age = timedelta(hours=PRICE_TIMELINE_MAX_AGE_HOURS)
        self._active_timeline_cache_token: object | None = None
        self._purchase_timeline_cache: dict[tuple[Any, ...], list[PriceInterval]] = {}
        self._feedin_timeline_cache: dict[tuple[Any, ...], list[PriceInterval]] = {}

        # Car peak limit tracking (15-minute hold after 5 minutes of sustained peak exceedance)
        self._car_peak_limited_until: datetime | None = None
        self._car_peak_limit_started_at: datetime | None = None

        # Solar forecast caching for sunny-day grid limit
        # After the configured start hour (default 20:00), we read tomorrow's
        # forecast and cache it.  The cache refreshes every hour so forecast
        # improvements are picked up, but persists through midnight until the
        # next start hour so the entity flip (tomorrow → today) doesn't affect
        # overnight charging decisions.
        self._cached_solar_forecast: float | None = None
        self._solar_forecast_cache_date: date | None = None  # local date when cached
        self._solar_forecast_cache_hour: int | None = None  # hour when last cached
        self._solar_forecast_source: str = "uninitialized"

        # Average threshold rolling-window calculator (owns hysteresis state)
        self._threshold_calculator = ThresholdCalculator(self)

        # Minimum charging window validator (OFF -> ON gate for car charging)
        self._charging_window_validator = ChargingWindowValidator(self)

        # Price timeline builder (purchase / feed-in timelines and export-slot selection)
        self._price_timeline_builder = PriceTimelineBuilder(self)

        # Transport cost resolver (built-in components and recorder-history lookup)
        self._transport_cost_resolver = TransportCostResolver(self)

        # Nord Pool service (fetch + TTL/LRU cache)
        self._nordpool_service = NordpoolService(self)

        # Arbitrage mode plan builder
        self._arbitrage_mode_planner = ArbitrageModePlanner(self)

        # Negative arbitrage buy plan builder
        self._negative_buy_planner = NegativeBuyPlanner(self)

        # Forecast summary (cheapest interval + best charging window)
        self._forecast_summary_calculator = ForecastSummaryCalculator(self)

        # Solar forecast resolver (hourly-refreshing cache for sunny-day logic)
        self._solar_forecast_service = SolarForecastService(self)

        # Entity status reporter (diagnostic view of configured entities)
        self._entity_status_reporter = EntityStatusReporter(self)

        # Manual override manager (user forced battery/car/charger_limit/grid_setpoint)
        self._manual_override_manager = ManualOverrideManager(self)

        # Runtime mode manager (car permissive + arbitrage mode toggles)
        self._runtime_mode_manager = RuntimeModeManager(self)

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=30),  # Maximum 30s updates (minimum 10s via entity changes)
        )

        self._setup_entity_listeners()

    # -- Average threshold hysteresis state (proxied to ThresholdCalculator) --
    @property
    def _average_threshold_enabled(self) -> bool:
        return self._threshold_calculator.enabled

    @_average_threshold_enabled.setter
    def _average_threshold_enabled(self, value: bool) -> None:
        self._threshold_calculator.enabled = bool(value)

    @property
    def _average_threshold_valid_count(self) -> int:
        return self._threshold_calculator.valid_count

    @_average_threshold_valid_count.setter
    def _average_threshold_valid_count(self, value: int) -> None:
        self._threshold_calculator.valid_count = int(value)

    async def async_initialize_persistent_state(self) -> None:
        """Initialize persisted state used by the coordinator."""
        self._car_permissive_mode_store = Store(
            self.hass,
            _CAR_PERMISSIVE_MODE_STORE_VERSION,
            f"{DOMAIN}.{self.entry.entry_id}.car_permissive_mode",
        )
        self._manual_override_store = Store(
            self.hass,
            _MANUAL_OVERRIDE_STORE_VERSION,
            f"{DOMAIN}.{self.entry.entry_id}.manual_overrides",
        )
        self._runtime_mode_store = Store(
            self.hass,
            _RUNTIME_MODE_STORE_VERSION,
            f"{DOMAIN}.{self.entry.entry_id}.runtime_modes",
        )
        self._battery_charging_state_store = Store(
            self.hass,
            _BATTERY_CHARGING_STATE_STORE_VERSION,
            f"{DOMAIN}.{self.entry.entry_id}.battery_charging_state",
        )
        await self._async_load_car_permissive_mode()
        await self._async_load_runtime_modes()
        await self._async_load_manual_overrides()
        await self._async_load_battery_charging_state()

    async def _async_load_battery_charging_state(self) -> None:
        """Restore battery grid-charging anti-flap state across restarts."""
        if self._battery_charging_state_store is None:
            return

        try:
            stored = await self._battery_charging_state_store.async_load()
        except Exception as err:
            if isinstance(err, (KeyboardInterrupt, SystemExit)):
                raise
            _LOGGER.warning(
                "Unable to load battery charging state for %s: %s",
                self.entry.entry_id,
                err,
            )
            return

        if not isinstance(stored, dict):
            return

        previous = stored.get("previous_battery_grid_charging")
        if isinstance(previous, bool):
            self._previous_battery_grid_charging = previous

        changed_at_raw = stored.get("battery_grid_charging_changed_at")
        if isinstance(changed_at_raw, str):
            parsed = dt_util.parse_datetime(changed_at_raw)
            if parsed is not None:
                self._battery_grid_charging_changed_at = dt_util.as_utc(parsed)

        locked_raw = stored.get("battery_grid_charging_locked_threshold")
        if isinstance(locked_raw, (int, float)):
            self._battery_grid_charging_locked_threshold = float(locked_raw)

    async def _async_persist_battery_charging_state(self) -> None:
        """Persist battery grid-charging anti-flap state for restart recovery."""
        if self._battery_charging_state_store is None:
            return

        payload: dict[str, Any] = {
            "previous_battery_grid_charging": bool(self._previous_battery_grid_charging),
            "battery_grid_charging_changed_at": (
                self._battery_grid_charging_changed_at.isoformat()
                if self._battery_grid_charging_changed_at is not None
                else None
            ),
            "battery_grid_charging_locked_threshold": (
                self._battery_grid_charging_locked_threshold
            ),
        }

        try:
            await self._battery_charging_state_store.async_save(payload)
        except Exception as err:
            if isinstance(err, (KeyboardInterrupt, SystemExit)):
                raise
            _LOGGER.warning(
                "Unable to persist battery charging state for %s: %s",
                self.entry.entry_id,
                err,
            )

    def get_manual_override(self, key: str) -> dict[str, Any] | None:
        """Delegate to the manual override manager collaborator."""
        return self._manual_override_manager.get(key)

    def is_arbitrage_mode_enabled(self) -> bool:
        """Delegate to the runtime mode manager collaborator."""
        return self._runtime_mode_manager.is_arbitrage_mode_enabled()

    def get_arbitrage_mode_state(self) -> dict[str, Any] | None:
        """Delegate to the runtime mode manager collaborator."""
        return self._runtime_mode_manager.get_arbitrage_mode_state()

    def is_negative_buy_mode_enabled(self) -> bool:
        """Delegate to the runtime mode manager collaborator."""
        return self._runtime_mode_manager.is_negative_buy_mode_enabled()

    def get_negative_buy_mode_state(self) -> dict[str, Any] | None:
        """Delegate to the runtime mode manager collaborator."""
        return self._runtime_mode_manager.get_negative_buy_mode_state()

    async def async_set_negative_buy_mode(self, reason: str | None = None) -> None:
        """Delegate to the runtime mode manager collaborator."""
        await self._runtime_mode_manager.set_negative_buy_mode(reason=reason)

    async def async_clear_negative_buy_mode(self) -> None:
        """Delegate to the runtime mode manager collaborator."""
        await self._runtime_mode_manager.clear_negative_buy_mode()

    async def async_set_car_permissive_mode(self, reason: str | None = None) -> None:
        """Delegate to the runtime mode manager collaborator."""
        await self._runtime_mode_manager.set_car_permissive_mode(reason=reason)

    async def async_clear_car_permissive_mode(self, reason: str | None = None) -> None:
        """Delegate to the runtime mode manager collaborator."""
        await self._runtime_mode_manager.clear_car_permissive_mode(reason=reason)

    async def async_set_arbitrage_mode(self, reason: str | None = None) -> None:
        """Delegate to the runtime mode manager collaborator."""
        await self._runtime_mode_manager.set_arbitrage_mode(reason=reason)

    async def async_clear_arbitrage_mode(self) -> None:
        """Delegate to the runtime mode manager collaborator."""
        await self._runtime_mode_manager.clear_arbitrage_mode()

    async def _async_load_manual_overrides(self) -> None:
        """Delegate to the manual override manager collaborator."""
        await self._manual_override_manager.load()

    async def _async_load_runtime_modes(self) -> None:
        """Delegate to the runtime mode manager collaborator."""
        await self._runtime_mode_manager.load_runtime_modes()

    async def _async_load_car_permissive_mode(self) -> None:
        """Delegate to the runtime mode manager collaborator."""
        await self._runtime_mode_manager.load_car_permissive_mode()

    async def _resolve_solar_forecast(self, entity_id: str | None = None) -> float | None:
        """Delegate to the solar forecast service collaborator."""
        return await self._solar_forecast_service.resolve(entity_id)

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
        entities_to_track = self._collect_tracked_entity_ids()
        self._tracked_entity_ids = set(entities_to_track)
        if entities_to_track:
            self._entity_unsub = async_track_state_change_event(
                self.hass, entities_to_track, self._handle_entity_change
            )

    def _collect_tracked_entity_ids(self) -> list[str]:
        """Return all entities that should trigger coordinator refreshes."""
        entities_to_track: list[str] = []

        for entity_key in [
            CONF_CURRENT_PRICE_ENTITY,
            CONF_HIGHEST_PRICE_ENTITY,
            CONF_LOWEST_PRICE_ENTITY,
            CONF_NEXT_PRICE_ENTITY,
        ]:
            if self.config.get(entity_key):
                entities_to_track.append(self.config[entity_key])

        if self.config.get(CONF_BATTERY_SOC_ENTITIES):
            entities_to_track.extend(self.config[CONF_BATTERY_SOC_ENTITIES])

        for entity_key in [
            CONF_SOLAR_PRODUCTION_ENTITY,
            CONF_HOUSE_CONSUMPTION_ENTITY,
            CONF_CAR_CHARGING_POWER_ENTITY,
            CONF_MONTHLY_GRID_PEAK_ENTITY,
            CONF_GRID_POWER_ENTITY,
        ]:
            if self.config.get(entity_key):
                entities_to_track.append(self.config[entity_key])

        if self.config.get(CONF_P1_TARIFF_ENTITY):
            entities_to_track.append(self.config[CONF_P1_TARIFF_ENTITY])

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

        return [entity_id for entity_id in entities_to_track if entity_id]

    def _has_builtin_transport_cost(self) -> bool:
        """Delegate to the transport-cost resolver collaborator."""
        return self._transport_cost_resolver.has_builtin()

    def _resolve_transport_cost(
        self,
        transport_lookup: list[dict[str, Any]] | None,
        start_time_utc: datetime,
        reference_now: datetime | None = None,
    ) -> float | None:
        """Delegate to the transport-cost resolver collaborator."""
        return self._transport_cost_resolver.resolve(
            transport_lookup, start_time_utc, reference_now
        )

    def _resolve_builtin_transport_cost(
        self,
        start_time_utc: datetime,
        reference_now: datetime | None = None,
    ) -> float:
        """Delegate to the transport-cost resolver collaborator."""
        return self._transport_cost_resolver.resolve_builtin(
            start_time_utc, reference_now
        )

    def _build_price_analysis_overrides(
        self,
        prices_today: dict[str, Any] | None,
        prices_tomorrow: dict[str, Any] | None,
        transport_lookup: list[dict[str, Any]] | None,
        current_transport_cost: float | None,
        now: datetime | None = None,
    ) -> dict[str, float | None] | None:
        """Build timestamp-aware price summary values from Nord Pool intervals."""
        if not prices_today and not prices_tomorrow:
            return None

        if now is None:
            now = dt_util.utcnow()

        multiplier = self.config.get(
            CONF_PRICE_ADJUSTMENT_MULTIPLIER, DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER
        )
        offset = self.config.get(
            CONF_PRICE_ADJUSTMENT_OFFSET, DEFAULT_PRICE_ADJUSTMENT_OFFSET
        )

        intervals: list[dict[str, Any]] = []

        def add_intervals(price_map: dict[str, Any] | None, source: str) -> None:
            if not isinstance(price_map, dict):
                return

            area_code = next(iter(price_map.keys()), None)
            if not area_code or not isinstance(price_map[area_code], list):
                return

            for interval in price_map[area_code]:
                start_raw = interval.get("start")
                if not start_raw:
                    continue

                start = parse_datetime_cached(start_raw)
                if start is None:
                    continue
                start_utc = dt_util.as_utc(start)

                raw_value = extract_price_from_interval(interval)
                if raw_value is None:
                    continue

                raw_price = raw_value / 1000
                adjusted_energy_price = (raw_price * multiplier) + offset
                transport_cost = self._resolve_transport_cost(
                    transport_lookup, start_utc, reference_now=now
                )
                if transport_cost is None:
                    transport_cost = current_transport_cost if current_transport_cost is not None else 0.0

                end_utc: datetime | None = None
                end_raw = interval.get("end")
                if end_raw:
                    parsed_end = parse_datetime_cached(end_raw)
                    if parsed_end is not None:
                        candidate_end = dt_util.as_utc(parsed_end)
                        if candidate_end > start_utc:
                            end_utc = candidate_end

                intervals.append(
                    {
                        "source": source,
                        "start": start_utc,
                        "end": end_utc,
                        "raw_price": raw_price,
                        "transport_cost": transport_cost,
                        "final_price": adjusted_energy_price + transport_cost,
                    }
                )

        add_intervals(prices_today, "today")
        add_intervals(prices_tomorrow, "tomorrow")

        if not intervals:
            return None

        intervals.sort(key=lambda item: item["start"])
        deltas = [
            intervals[idx + 1]["start"] - intervals[idx]["start"]
            for idx in range(len(intervals) - 1)
            if intervals[idx + 1]["start"] > intervals[idx]["start"]
        ]
        inferred_resolution = min(deltas) if deltas else timedelta(minutes=15)

        for idx, item in enumerate(intervals):
            if item["end"] is not None:
                continue
            next_start = intervals[idx + 1]["start"] if idx + 1 < len(intervals) else None
            item["end"] = (
                next_start
                if next_start is not None and next_start > item["start"]
                else item["start"] + inferred_resolution
            )

        current_interval = next(
            (item for item in intervals if item["start"] <= now < item["end"]),
            None,
        )
        next_interval = next((item for item in intervals if item["start"] > now), None)

        today_intervals = [item for item in intervals if item["source"] == "today"]
        highest_today = max(today_intervals, key=lambda item: item["final_price"]) if today_intervals else None
        lowest_today = min(today_intervals, key=lambda item: item["final_price"]) if today_intervals else None

        if current_interval is None and highest_today is None and next_interval is None:
            return None

        return {
            "current_price": current_interval["final_price"] if current_interval else None,
            "highest_price": highest_today["final_price"] if highest_today else None,
            "lowest_price": lowest_today["final_price"] if lowest_today else None,
            "next_price": next_interval["final_price"] if next_interval else None,
            "raw_current_price": current_interval["raw_price"] if current_interval else None,
            "raw_highest_price": highest_today["raw_price"] if highest_today else None,
            "raw_lowest_price": lowest_today["raw_price"] if lowest_today else None,
            "raw_next_price": next_interval["raw_price"] if next_interval else None,
            "transport_cost": current_interval["transport_cost"] if current_interval else current_transport_cost,
        }

    async def async_set_manual_override(
        self,
        target: str,
        value: bool | None,
        duration: timedelta | None,
        reason: str | None,
        charger_limit: int | None = None,
        grid_setpoint: int | None = None,
    ) -> None:
        """Delegate to the manual override manager collaborator."""
        await self._manual_override_manager.set_override(
            target, value, duration, reason, charger_limit, grid_setpoint
        )

    async def async_clear_manual_override(self, target: str | None = None) -> None:
        """Delegate to the manual override manager collaborator."""
        await self._manual_override_manager.clear(target)

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
        try:
            monthly_peak_value = max(0.0, float(monthly_peak or 0))
        except (TypeError, ValueError):
            monthly_peak_value = 0.0
        if is_in_month_peak_transition_window(now=now):
            monthly_peak_value = 0.0
        effective_peak = max(monthly_peak_value, base_grid_setpoint)
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
        """Delegate to the manual override manager collaborator."""
        return self._manual_override_manager.apply(decision)

    def _update_battery_charging_state_tracking(
        self,
        automatic_battery_grid_charging: bool,
        override_targets: set[str],
        effective_threshold: float | None = None,
    ) -> None:
        """Track automatic battery charging state for anti-flapping logic.

        Locks the price threshold at OFF->ON transitions so the anti-flap hold
        compares against the threshold that was acceptable when charging
        started, not the current SOC-relaxed threshold (which can drift down
        as SOC rises and prematurely terminate the hold).
        """
        if "battery_grid_charging" in override_targets:
            self._previous_battery_grid_charging = False
            self._battery_grid_charging_changed_at = None
            self._battery_grid_charging_locked_threshold = None
            return

        if automatic_battery_grid_charging != self._previous_battery_grid_charging:
            self._battery_grid_charging_changed_at = dt_util.utcnow()
            if automatic_battery_grid_charging:
                # OFF -> ON: capture the threshold that justified the start
                self._battery_grid_charging_locked_threshold = (
                    float(effective_threshold)
                    if effective_threshold is not None
                    else None
                )
            else:
                # ON -> OFF: hold expired or genuinely declined; clear the lock
                self._battery_grid_charging_locked_threshold = None
        self._previous_battery_grid_charging = automatic_battery_grid_charging

    def _build_price_timeline(
        self,
        prices_today: dict[str, Any] | None,
        prices_tomorrow: dict[str, Any] | None,
        transport_lookup: list[dict[str, Any]] | None,
        current_transport_cost: float | None,
        now: datetime,
    ) -> list[PriceInterval]:
        """Delegate to the price-timeline builder collaborator."""
        cache_key = self._purchase_timeline_cache_key(
            prices_today,
            prices_tomorrow,
            transport_lookup,
            current_transport_cost,
            now,
        )
        if cache_key is not None and cache_key in self._purchase_timeline_cache:
            return self._purchase_timeline_cache[cache_key]

        timeline = self._price_timeline_builder.build_purchase(
            prices_today,
            prices_tomorrow,
            transport_lookup,
            current_transport_cost,
            now,
        )
        if cache_key is not None:
            self._purchase_timeline_cache[cache_key] = timeline
        return timeline

    def _build_feedin_price_timeline(
        self,
        prices_today: dict[str, Any] | None,
        prices_tomorrow: dict[str, Any] | None,
        now: datetime,
    ) -> list[PriceInterval]:
        """Delegate to the price-timeline builder collaborator."""
        cache_key = self._feedin_timeline_cache_key(prices_today, prices_tomorrow, now)
        if cache_key is not None and cache_key in self._feedin_timeline_cache:
            return self._feedin_timeline_cache[cache_key]

        timeline = self._price_timeline_builder.build_feedin(
            prices_today, prices_tomorrow, now
        )
        if cache_key is not None:
            self._feedin_timeline_cache[cache_key] = timeline
        return timeline

    def _purchase_timeline_cache_key(
        self,
        prices_today: dict[str, Any] | None,
        prices_tomorrow: dict[str, Any] | None,
        transport_lookup: list[dict[str, Any]] | None,
        current_transport_cost: float | None,
        now: datetime,
    ) -> tuple[Any, ...] | None:
        """Return a per-update cache key for purchase price timelines.

        Within a single update cycle (pinned by ``_active_timeline_cache_token``)
        the price and transport-lookup containers passed in are the same dict
        / list objects living on ``self.data``. Object identity is therefore a
        cheap substitute for hashing their full contents. ``now`` is still part
        of the key because timeline construction filters against it and uses it
        as the current-slot transport-cost reference.
        """
        if self._active_timeline_cache_token is None:
            return None

        return (
            self._active_timeline_cache_token,
            id(prices_today),
            id(prices_tomorrow),
            id(transport_lookup),
            current_transport_cost,
            now.replace(microsecond=0).isoformat(),
            self.config.get(CONF_PRICE_ADJUSTMENT_MULTIPLIER),
            self.config.get(CONF_PRICE_ADJUSTMENT_OFFSET),
        )

    def _feedin_timeline_cache_key(
        self,
        prices_today: dict[str, Any] | None,
        prices_tomorrow: dict[str, Any] | None,
        now: datetime,
    ) -> tuple[Any, ...] | None:
        """Return a per-update cache key for feed-in price timelines."""
        if self._active_timeline_cache_token is None:
            return None

        return (
            self._active_timeline_cache_token,
            id(prices_today),
            id(prices_tomorrow),
            now.replace(microsecond=0).isoformat(),
            self.config.get(CONF_FEEDIN_ADJUSTMENT_MULTIPLIER),
            self.config.get(CONF_FEEDIN_ADJUSTMENT_OFFSET),
        )

    def _select_export_slots(
        self,
        timeline: list[PriceInterval],
        now: datetime,
        required_duration: timedelta,
        minimum_price: float,
        latest_end: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Delegate to the price-timeline builder collaborator."""
        return self._price_timeline_builder.select_export_slots(
            timeline,
            now,
            required_duration,
            minimum_price,
            latest_end=latest_end,
        )

    def _arbitrage_mode_deadline(self, now: datetime) -> datetime:
        """Delegate to the arbitrage mode planner collaborator."""
        return self._arbitrage_mode_planner.deadline(now)

    def _calculate_arbitrage_mode_plan(self, data: dict[str, Any]) -> dict[str, Any]:
        """Delegate to the arbitrage mode planner collaborator."""
        return self._arbitrage_mode_planner.build_plan(data)

    def _select_buy_slots(
        self,
        timeline: list[PriceInterval],
        now: datetime,
        required_duration: timedelta,
        maximum_price: float,
        latest_end: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Delegate to the price-timeline builder collaborator."""
        return self._price_timeline_builder.select_buy_slots(
            timeline,
            now,
            required_duration,
            maximum_price,
            latest_end=latest_end,
        )

    def _negative_buy_deadline(self, now: datetime) -> datetime:
        """Delegate to the negative buy planner collaborator."""
        return self._negative_buy_planner.deadline(now)

    def _calculate_negative_buy_plan(self, data: dict[str, Any]) -> dict[str, Any]:
        """Delegate to the negative buy planner collaborator."""
        return self._negative_buy_planner.build_plan(data)

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
        tracked_entity_ids = self._tracked_entity_ids or set(self._collect_tracked_entity_ids())
        if entity_id in tracked_entity_ids:
            # Use async task to avoid blocking the callback
            # Note: Throttling is handled atomically in _async_handle_throttled_update
            # to prevent race conditions from multiple rapid events
            self.hass.async_create_task(self._async_handle_throttled_update(entity_id))

    async def _async_handle_throttled_update(self, entity_id: str) -> None:
        """Handle entity update with atomic throttling check."""
        should_refresh = False
        async with self._update_lock:
            now = dt_util.utcnow()

            # Apply minimum interval throttling (atomic check-and-set)
            if (self._last_entity_update is None or
                now - self._last_entity_update >= self._min_update_interval):

                self._last_entity_update = now
                should_refresh = True
            else:
                time_remaining = (self._last_entity_update + self._min_update_interval - now).total_seconds()
                _LOGGER.debug("Entity update skipped for %s (throttled, %.1fs remaining)",
                            entity_id, time_remaining)

        if should_refresh:
            await self.async_request_refresh()
            _LOGGER.debug("Entity update triggered for %s (throttled to %ds minimum)",
                        entity_id, self._min_update_interval.total_seconds())

    def _get_current_price_interval_start(self) -> datetime:
        """Get the start time of the active price interval."""
        now = dt_util.utcnow()

        if self._last_price_timeline:
            for start, end, _price in self._last_price_timeline:
                if start <= now < end:
                    return start

        # Fall back to the legacy fixed-resolution boundary when no timeline is available.
        minutes = (now.minute // PRICE_INTERVAL_MINUTES) * PRICE_INTERVAL_MINUTES
        return now.replace(minute=minutes, second=0, microsecond=0)

    def _should_use_average_threshold(self, average_threshold: float | None) -> bool:
        """Return whether the rolling average threshold is active for decisions."""
        return bool(
            self.config.get(CONF_USE_AVERAGE_THRESHOLD, DEFAULT_USE_AVERAGE_THRESHOLD)
            and average_threshold is not None
            and self._average_threshold_enabled
        )

    def _clean_expired_nordpool_cache(self) -> None:
        """Delegate to the Nord Pool service collaborator."""
        self._nordpool_service.clean_expired_cache()

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
        self._active_timeline_cache_token = object()
        self._purchase_timeline_cache.clear()
        self._feedin_timeline_cache.clear()
        try:
            # Clean expired cache entries periodically
            self._clean_expired_nordpool_cache()

            data = await self._fetch_all_data()

            # Determine the current price threshold (with dynamic/average logic)
            average_threshold = data.get("average_threshold")
            average_threshold_active = self._should_use_average_threshold(average_threshold)
            data["average_threshold_active"] = average_threshold_active
            data["average_threshold_candidate"] = average_threshold
            if not average_threshold_active:
                data["average_threshold"] = None

            if average_threshold_active:
                current_threshold = average_threshold
            else:
                current_threshold = self.config.get(CONF_PRICE_THRESHOLD, DEFAULT_PRICE_THRESHOLD)

            # Update battery threshold snapshot if we've entered a new 15-min interval
            self._update_battery_threshold_snapshot_if_needed(current_threshold)

            # Add previous car charging state for hysteresis logic
            data["previous_car_charging"] = self._previous_car_charging
            data["previous_battery_grid_charging"] = self._previous_battery_grid_charging
            if self._battery_grid_charging_changed_at is not None:
                data["battery_grid_charging_state_age_seconds"] = (
                    dt_util.utcnow() - self._battery_grid_charging_changed_at
                ).total_seconds()
            if self._battery_grid_charging_locked_threshold is not None:
                data["battery_grid_charging_locked_threshold"] = (
                    self._battery_grid_charging_locked_threshold
                )

            # Pass the stable threshold snapshot to decision engine
            if self._battery_threshold_snapshot is not None:
                data["battery_stable_threshold"] = self._battery_threshold_snapshot

            # Update peak import limit state based on current grid power
            self._update_peak_limit_state(data)

            arbitrage_mode_plan = self._calculate_arbitrage_mode_plan(data)
            data["arbitrage_mode_plan"] = arbitrage_mode_plan
            data["arbitrage_mode_enabled"] = arbitrage_mode_plan.get("enabled", False)
            data["arbitrage_mode_active"] = arbitrage_mode_plan.get("active", False)
            data["arbitrage_mode_reason"] = arbitrage_mode_plan.get("reason")
            data["arbitrage_mode_reserve_soc"] = arbitrage_mode_plan.get("reserve_soc")
            data["arbitrage_mode_export_power"] = arbitrage_mode_plan.get("export_power", 0)

            negative_buy_plan = self._calculate_negative_buy_plan(data)
            data["negative_buy_plan"] = negative_buy_plan
            data["negative_buy_mode_enabled"] = negative_buy_plan.get("enabled", False)
            data["negative_buy_mode_active"] = negative_buy_plan.get("active", False)
            data["negative_buy_mode_reason"] = negative_buy_plan.get("reason")
            data["negative_buy_import_power"] = negative_buy_plan.get("import_power", 0)
            data["negative_buy_curtail_solar"] = negative_buy_plan.get(
                "solar_curtail_active", False
            )

            charging_decision = await self.decision_engine.evaluate_charging_decision(data)
            automatic_battery_grid_charging = bool(
                charging_decision.get("battery_grid_charging", False)
            )
            automatic_effective_threshold = charging_decision.get(
                "battery_effective_threshold"
            )
            charging_decision, override_targets = self._apply_manual_overrides(charging_decision)

            if override_targets:
                charging_decision = self.decision_engine.recalculate_after_override(
                    data, charging_decision, override_targets
                )

            data.update(charging_decision)

            # Update previous car charging state
            self._previous_car_charging = charging_decision.get("car_grid_charging", False)
            previous_locked_threshold = self._battery_grid_charging_locked_threshold
            previous_changed_at = self._battery_grid_charging_changed_at
            previous_state = self._previous_battery_grid_charging
            self._update_battery_charging_state_tracking(
                automatic_battery_grid_charging,
                override_targets,
                effective_threshold=automatic_effective_threshold,
            )
            if (
                self._previous_battery_grid_charging != previous_state
                or self._battery_grid_charging_changed_at != previous_changed_at
                or self._battery_grid_charging_locked_threshold != previous_locked_threshold
            ):
                await self._async_persist_battery_charging_state()

            # Check data availability and handle notifications
            await self._check_data_availability(data)

            return data

        except Exception as err:
            if isinstance(err, (KeyboardInterrupt, SystemExit)):
                raise
            raise UpdateFailed(f"Error communicating with entities: {err}") from err
        finally:
            self._active_timeline_cache_token = None
            self._purchase_timeline_cache.clear()
            self._feedin_timeline_cache.clear()

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

                # Grid power: from per-phase sensor (optional, positive = import)
                grid_power_entity = phase_config.get(CONF_PHASE_GRID_POWER_ENTITY)
                grid_power_val = await self._get_state_value(grid_power_entity)

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
                    "grid_power": grid_power_val,
                    "battery_power": battery_power_val,  # Actual battery power (W, negative = charging)
                    "solar_surplus": phase_surplus,
                    "has_car_sensor": car_entity is not None,
                    "has_grid_power_sensor": grid_power_entity is not None,
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
        data["previous_grid_power"] = (
            self.data.get("grid_power") if self.data else None
        )

        # Preserve prior inverter target so the derating controller can hold or
        # release curtailment gradually instead of jumping back to max power.
        data["previous_inverter_derating_target"] = (
            self.data.get("inverter_derating_target") if self.data else None
        )
        data["previous_inverter_derating_unreached_since"] = (
            self.data.get("inverter_derating_unreached_since") if self.data else None
        )
        data["inverter_derating_evaluated_at"] = dt_util.utcnow()

        # Solar forecast with daily caching for stable overnight decisions.
        # After the configured start hour: read the forecast entity (tomorrow's
        # production) and cache it.  This cached value is used for all decisions
        # until the next day's start hour, so midnight entity flips don't affect
        # overnight charging.  Before the start hour on the first day (no cache
        # yet), use the live value.
        solar_forecast_entity = self.config.get(CONF_SOLAR_FORECAST_ENTITY_TOMORROW)
        if solar_forecast_entity:
            data["solar_forecast_production"] = await self._resolve_solar_forecast(
                solar_forecast_entity
            )
        else:
            data["solar_forecast_production"] = None
            self._solar_forecast_source = "missing_tomorrow_entity"
        data["solar_forecast_source"] = self._solar_forecast_source

        # Preserve car charging locked threshold across updates (for threshold continuity)
        data["car_charging_locked_threshold"] = self.data.get("car_charging_locked_threshold") if self.data else None

        # Expose car permissive mode state in data dict (source of truth is self._car_permissive_mode_active)
        data["car_permissive_mode_active"] = self._car_permissive_mode_active



        # Transport cost: use built-in components if configured, otherwise legacy entity
        if self._has_builtin_transport_cost():
            # Built-in mode: no external entity or history lookup needed
            data["transport_cost"] = self._resolve_builtin_transport_cost(dt_util.utcnow())
            data["transport_cost_lookup"] = []
            data["transport_cost_status"] = "builtin"
            # Store current P1 tariff code for diagnostics
            p1_entity = self.config.get(CONF_P1_TARIFF_ENTITY)
            if p1_entity:
                state = self.hass.states.get(p1_entity)
                data["p1_tariff_code"] = state.state if state and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN) else None
            else:
                data["p1_tariff_code"] = None
        else:
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

        if self._has_builtin_transport_cost():
            transport_lookup: list[dict[str, Any]] = []
            transport_status = "builtin"
        else:
            transport_lookup, transport_status = await self._get_transport_cost_lookup(
                data.get("transport_cost")
            )
        data["transport_cost_lookup"] = transport_lookup
        data["transport_cost_status"] = transport_status
        data["price_analysis_overrides"] = self._build_price_analysis_overrides(
            data.get("nordpool_prices_today"),
            data.get("nordpool_prices_tomorrow"),
            transport_lookup,
            data.get("transport_cost"),
        )

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
        """Delegate to the entity status reporter collaborator."""
        return await self._entity_status_reporter.get_state_value(entity_id)

    def get_all_entity_statuses(self) -> dict[str, Any]:
        """Delegate to the entity status reporter collaborator."""
        return self._entity_status_reporter.get_all_entity_statuses()

    async def _fetch_nordpool_prices(self, config_entry_id: str, day: str) -> dict[str, Any] | None:
        """Delegate to the Nord Pool service collaborator."""
        return await self._nordpool_service.fetch_prices(config_entry_id, day)

    def _calculate_average_threshold(
        self,
        prices_today: dict[str, Any] | None,
        prices_tomorrow: dict[str, Any] | None,
        transport_lookup: list[dict[str, Any]] | None
    ) -> float | None:
        """Delegate to the threshold calculator collaborator."""
        return self._threshold_calculator.calculate(
            prices_today, prices_tomorrow, transport_lookup
        )

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
        """Delegate to the charging-window validator collaborator."""
        return self._charging_window_validator.check(
            prices_today,
            prices_tomorrow,
            transport_lookup,
            current_transport_cost,
            average_threshold=average_threshold,
            permissive_mode_active=permissive_mode_active,
            permissive_multiplier=permissive_multiplier,
        )

    @staticmethod
    def _compute_price_data_hash(
        prices_today: dict[str, Any] | None,
        prices_tomorrow: dict[str, Any] | None,
    ) -> str:
        """Compute a hash of the price data for cache invalidation.

        This ensures the price timeline cache is invalidated when the underlying
        price data changes, not just when it expires by age.
        """
        # Create a deterministic representation of the price data
        data_repr = json.dumps(
            {"today": prices_today, "tomorrow": prices_tomorrow},
            sort_keys=True,
            default=str,  # Handle datetime objects
        )
        return hashlib.md5(data_repr.encode()).hexdigest()

    def _calculate_forecast_summary(
        self,
        prices_today: dict[str, Any] | None,
        prices_tomorrow: dict[str, Any] | None,
        transport_lookup: list[dict[str, Any]] | None,
        current_transport_cost: float | None,
        minimum_average_threshold: float | None,
    ) -> dict[str, Any]:
        """Delegate to the forecast summary collaborator."""
        return self._forecast_summary_calculator.calculate(
            prices_today,
            prices_tomorrow,
            transport_lookup,
            current_transport_cost,
            minimum_average_threshold,
        )

    async def _get_transport_cost_lookup(
        self, current_transport_cost: float | None = None
    ) -> tuple[list[dict[str, Any]], str]:
        """Delegate to the transport-cost resolver collaborator."""
        return await self._transport_cost_resolver.get_lookup(current_transport_cost)

    async def _check_data_availability(self, data: dict[str, Any]) -> None:
        """Check data availability and send notifications if needed."""
        now = dt_util.utcnow()

        # Check if critical data is available
        data_is_available = self._is_data_available(data)

        if data_is_available:
            # Data is available - reset tracking
            self._last_successful_update = now
            if self._data_unavailable_since is not None and self._notification_sent:
                # Data was unavailable but is now available - send recovery notification
                unavailable_duration = now - self._data_unavailable_since
                await self._send_notification(
                    "Electricity Planner Data Restored",
                    f"Nord Pool data has been restored after {unavailable_duration.total_seconds():.0f} seconds. "
                    f"Charging decisions are now active.",
                    "electricity_planner_data_restored"
                )
                _LOGGER.info("Data availability restored after %.1f seconds", unavailable_duration.total_seconds())
            if self._data_unavailable_since is not None:
                self._data_unavailable_since = None
            self._notification_sent = False
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
            if isinstance(err, (KeyboardInterrupt, SystemExit)):
                raise
            _LOGGER.error("Failed to send notification: %s", err)

    def is_data_available(self) -> bool:
        """Public helper for consumers needing availability."""
        if not self.data:
            return False
        return self._is_data_available(self.data)

    @property
    def last_successful_update(self) -> datetime | None:
        """Expose last successful update timestamp."""
        return self._last_successful_update

    @property
    def data_unavailable_since(self) -> datetime | None:
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
