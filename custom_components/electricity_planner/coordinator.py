"""Data coordinator for Electricity Planner."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections import deque
from datetime import date, datetime, timedelta
from math import isfinite
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .arbitrage_mode import ArbitrageModePlanner
from .charging_window import ChargingWindowValidator
from .const import (
    BATTERY_CAPACITY_FALLBACK_WEIGHT,
    BATTERY_SOC_DECIMAL_THRESHOLD,
    CONF_BASE_GRID_SETPOINT,
    CONF_BATTERY_CAPACITIES,
    CONF_BATTERY_PHASE_ASSIGNMENTS,
    CONF_BATTERY_SOC_ENTITIES,
    CONF_BUY_VAT_MULTIPLIER,
    CONF_CAR_CHARGING_POWER_ENTITY,
    CONF_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
    CONF_CURRENT_PRICE_ENTITY,
    CONF_FEEDIN_ADJUSTMENT_MULTIPLIER,
    CONF_FEEDIN_ADJUSTMENT_OFFSET,
    CONF_GRID_IMPORT_NEGATIVE_FEEDBACK,
    CONF_GRID_POWER_ENTITY,
    CONF_HIGHEST_PRICE_ENTITY,
    CONF_HOUSE_CONSUMPTION_ENTITY,
    CONF_LOWEST_PRICE_ENTITY,
    CONF_MAX_BATTERY_POWER,
    CONF_MAX_SOC_THRESHOLD,
    CONF_MIN_CAR_CHARGING_THRESHOLD,
    CONF_MIN_SOC_THRESHOLD,
    CONF_MONTHLY_GRID_PEAK_ENTITY,
    CONF_NEXT_PRICE_ENTITY,
    CONF_NORDPOOL_CONFIG_ENTRY,
    CONF_P1_TARIFF_ENTITY,
    CONF_PHASE_BATTERY_POWER_ENTITY,
    CONF_PHASE_CAR_ENTITY,
    CONF_PHASE_CONSUMPTION_ENTITY,
    CONF_PHASE_GRID_POWER_ENTITY,
    CONF_PHASE_MODE,
    CONF_PHASE_NAME,
    CONF_PHASE_SOLAR_ENTITY,
    CONF_PHASES,
    CONF_PRICE_ADJUSTMENT_MULTIPLIER,
    CONF_PRICE_ADJUSTMENT_OFFSET,
    CONF_PRICE_THRESHOLD,
    CONF_SOLAR_FORECAST_ENTITY_TOMORROW,
    CONF_SOLAR_PRODUCTION_ENTITY,
    CONF_TRANSPORT_COST_ENTITY,
    CONF_USE_AVERAGE_THRESHOLD,
    DEFAULT_BASE_GRID_SETPOINT,
    DEFAULT_BUY_VAT_MULTIPLIER,
    DEFAULT_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
    DEFAULT_MAX_BATTERY_POWER,
    DEFAULT_MAX_SOC,
    DEFAULT_MIN_CAR_CHARGING_THRESHOLD,
    DEFAULT_MIN_SOC,
    DEFAULT_PHASE_NAMES,
    DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER,
    DEFAULT_PRICE_ADJUSTMENT_OFFSET,
    DEFAULT_PRICE_THRESHOLD,
    DEFAULT_USE_AVERAGE_THRESHOLD,
    DOMAIN,
    MIN_UPDATE_INTERVAL_SECONDS,
    NORDPOOL_CACHE_MAX_SIZE,
    PEAK_LIMIT_DURATION_MINUTES,
    PEAK_MONITORING_DURATION_MINUTES,
    PEAK_THRESHOLD_MULTIPLIER,
    PHASE_IDS,
    PHASE_MODE_SINGLE,
    PHASE_MODE_THREE,
    PRICE_INTERVAL_MINUTES,
    PRICE_TIMELINE_MAX_AGE_HOURS,
)
from .decision_engine import ChargingDecisionEngine
from .energy_ledger import BatteryEnergyLedger, EnergyOrigin
from .entity_status import EntityStatusReporter
from .forecast_summary import ForecastSummaryCalculator
from .grid_signs import sensor_grid_import_w
from .helpers import (
    PriceInterval,
    extract_price_from_interval,
    is_in_month_peak_transition_window,
    parse_datetime_cached,
)
from .manual_overrides import ManualOverrideManager
from .negative_buy import NegativeBuyPlanner
from .nordpool_service import NordpoolService
from .price_timeline import PriceTimelineBuilder
from .runtime_modes import RuntimeModeManager
from .solar_forecast import SolarForecastService
from .threshold_calculator import ThresholdCalculator
from .transport_cost import TransportCostResolver

_LOGGER = logging.getLogger(__name__)
_MANUAL_OVERRIDE_STORE_VERSION = 1
_CAR_PERMISSIVE_MODE_STORE_VERSION = 1
_RUNTIME_MODE_STORE_VERSION = 1
_BATTERY_CHARGING_STATE_STORE_VERSION = 1
_ENERGY_LEDGER_STORE_VERSION = 1
# Battery energy below this is noise from SOC quantisation, not a real movement.
_ENERGY_LEDGER_TOLERANCE_KWH = 0.001
# Longest gap between two accepted observations that the ledger may survive.
# Beyond it (restart, downtime, stalled refresh) the battery may have charged
# and discharged several times from several sources, so *no* existing lot can
# still be trusted -- not just the newest delta.  The whole ledger is reset to
# unknown energy, which can never be exported.
_ENERGY_LEDGER_MAX_ATTRIBUTION_GAP_SECONDS = 900
# Headroom on the commanded power when checking whether a source could have
# delivered the observed delta, covering sensor timing jitter.
_ENERGY_LEDGER_POWER_TOLERANCE = 1.25
# Ledger writes are coalesced: the ledger moves every refresh and must not
# translate into a flash write every refresh.
_ENERGY_LEDGER_SAVE_DELAY_SECONDS = 300
_INVERTER_DERATING_EXPORT_AVERAGE_WINDOW = timedelta(minutes=3)
_GRID_TRACKING_AVERAGE_WINDOW = timedelta(minutes=3)


def _coerce_ledger_inventory(
    raw: object,
) -> tuple[tuple[str, float], ...] | None:
    """Return the persisted battery inventory fingerprint, or None if unusable."""
    if not isinstance(raw, list):
        return None
    inventory: list[tuple[str, float]] = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            return None
        entity_id, capacity = item
        if (
            not isinstance(entity_id, str)
            or not isinstance(capacity, (int, float))
            or isinstance(capacity, bool)
            or not isfinite(capacity)
            or capacity <= 0
        ):
            return None
        inventory.append((entity_id, round(float(capacity), 6)))
    return tuple(sorted(inventory))


class ElectricityPlannerCoordinator(DataUpdateCoordinator):
    """Coordinator for electricity planner data."""

    @staticmethod
    def _time_weighted_power_average(
        history: deque[tuple[datetime, float]],
        window_start: datetime,
        window_end: datetime,
    ) -> float | None:
        """Average piecewise-constant meter readings over an exact time window."""
        if window_end <= window_start or not history:
            return None

        weighted_sum = 0.0
        covered_seconds = 0.0
        for index, (sample_time, power) in enumerate(history):
            segment_start = max(sample_time, window_start)
            next_time = (
                history[index + 1][0] if index + 1 < len(history) else window_end
            )
            segment_end = min(next_time, window_end)
            duration = (segment_end - segment_start).total_seconds()
            if duration > 0:
                weighted_sum += power * duration
                covered_seconds += duration

        return weighted_sum / covered_seconds if covered_seconds > 0 else None

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
        # Virtual battery-energy ledger.  ``_ledger_initialized`` gates every
        # read: an absent, empty or untrusted ledger must never let energy be
        # exported.  The charge intent is deliberately *not* persisted, so a
        # restart can never attribute an accumulated delta to a stale source.
        self._energy_ledger_store: Store[dict[str, Any]] | None = None
        self._energy_ledger = BatteryEnergyLedger()
        self._ledger_initialized = False
        self._ledger_last_energy_kwh: float | None = None
        # Last accepted observation (advanced even when nothing moved) and the
        # moment the energy baseline itself was last set.  They differ while
        # sub-quantisation drift accumulates: the first bounds how stale the
        # ledger is, the second bounds how much energy a source could have
        # delivered since the baseline.
        self._ledger_last_observed_at: datetime | None = None
        self._ledger_baseline_at: datetime | None = None
        self._ledger_inventory: tuple[tuple[str, float], ...] | None = None
        self._ledger_charge_intent: dict[str, Any] | None = None
        self._ledger_status: str = "uninitialized"

        # Price interval tracking for threshold stability
        self._current_price_interval_start: datetime | None = None
        self._battery_threshold_snapshot: float | None = None
        self._battery_threshold_snapshot_source: str | None = None
        self._last_config_hash: int | None = (
            None  # Track config changes for threshold updates
        )

        # Keep a short, in-memory grid-power history for the PV derating
        # controller.  A rolling average avoids curtailing solar because of a
        # single P1-meter update near the export deadband.
        self._inverter_derating_grid_power_history: deque[tuple[datetime, float]] = (
            deque()
        )

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
        self._last_price_timeline_data_hash: str | None = (
            None  # Hash of price data used to build timeline
        )
        self._price_timeline_max_age = timedelta(hours=PRICE_TIMELINE_MAX_AGE_HOURS)
        self._active_timeline_cache_token: object | None = None
        self._purchase_timeline_cache: dict[tuple[Any, ...], list[PriceInterval]] = {}
        self._feedin_timeline_cache: dict[tuple[Any, ...], list[PriceInterval]] = {}

        # Car peak limit tracking (15-minute hold after 5 minutes of sustained peak exceedance)
        self._car_peak_limited_until: datetime | None = None
        self._car_peak_limit_started_at: datetime | None = None
        self._grid_tracking_charger_limit: int | None = None
        self._grid_tracking_last_adjustment: datetime | None = None
        self._grid_tracking_waiting_for_application: bool = False
        # A cap restored from disk describes the state before the restart.  It
        # is kept for diagnostics only and must never throttle the charger
        # until this cycle has produced its own P1 average, because the
        # in-memory averaging window does not survive a restart.
        self._grid_tracking_cap_restored_unvalidated: bool = False
        # EV grid tracking has its own window.  It must not inherit P1
        # samples gathered while the car decision was Off, otherwise a new
        # charging window could react to stale import data immediately.
        self._grid_tracking_power_history: deque[tuple[datetime, float]] = deque()
        self._grid_tracking_window_started_at: datetime | None = None

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
            update_interval=timedelta(
                seconds=30
            ),  # Maximum 30s updates (minimum 10s via entity changes)
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
        self._energy_ledger_store = Store(
            self.hass,
            _ENERGY_LEDGER_STORE_VERSION,
            f"{DOMAIN}.{self.entry.entry_id}.energy_ledger",
            atomic_writes=True,
        )
        await self._async_load_car_permissive_mode()
        await self._async_load_runtime_modes()
        await self._async_load_manual_overrides()
        await self._async_load_battery_charging_state()
        await self._async_load_energy_ledger()

    @property
    def energy_ledger(self) -> BatteryEnergyLedger:
        """Return the virtual battery-energy ledger."""
        return self._energy_ledger

    @property
    def energy_ledger_initialized(self) -> bool:
        """Return True once a trusted battery observation has been recorded."""
        return self._ledger_initialized

    @property
    def energy_ledger_status(self) -> str:
        """Return the last ledger reconciliation outcome, for reasons and diagnostics."""
        return self._ledger_status

    def energy_ledger_diagnostics(self) -> dict[str, Any]:
        """Return a compact ledger snapshot for diagnostics."""
        diagnostics: dict[str, Any] = {
            "initialized": self._ledger_initialized,
            "status": self._ledger_status,
            "observed_energy_kwh": (
                None
                if self._ledger_last_energy_kwh is None
                else round(self._ledger_last_energy_kwh, 6)
            ),
            "observed_at": (
                None
                if self._ledger_last_observed_at is None
                else self._ledger_last_observed_at.isoformat()
            ),
            "inventory": (
                None
                if self._ledger_inventory is None
                else [list(item) for item in self._ledger_inventory]
            ),
            "charge_intent": None,
        }
        diagnostics.update(self._energy_ledger.summary())
        intent = self._ledger_charge_intent
        if intent is not None:
            diagnostics["charge_intent"] = {
                "origin": intent["origin"].value,
                "at": intent["at"].isoformat(),
                "buy_cost_eur_per_kwh": intent["buy_cost"],
                "power_w": intent["power_w"],
            }
        return diagnostics

    def _energy_ledger_payload(self) -> dict[str, Any]:
        """Build the durable ledger payload.

        The charge intent is intentionally absent: it describes a command that
        this process issued moments ago, and replaying it after a restart would
        attribute a whole downtime's worth of charging to one stale source.
        """
        return {
            "ledger": self._energy_ledger.as_dict(),
            "observed_energy_kwh": self._ledger_last_energy_kwh,
            "observed_at": (
                None
                if self._ledger_last_observed_at is None
                else self._ledger_last_observed_at.isoformat()
            ),
            "inventory": (
                None
                if self._ledger_inventory is None
                else [
                    [entity_id, capacity]
                    for entity_id, capacity in self._ledger_inventory
                ]
            ),
        }

    def _reset_energy_ledger(self, status: str) -> None:
        """Fail closed: forget the ledger until a trusted observation arrives."""
        self._energy_ledger = BatteryEnergyLedger()
        self._ledger_initialized = False
        self._ledger_last_energy_kwh = None
        self._ledger_last_observed_at = None
        self._ledger_baseline_at = None
        self._ledger_inventory = None
        self._ledger_charge_intent = None
        self._ledger_status = status

    async def _async_load_energy_ledger(self) -> None:
        """Restore virtual lots; anything untrusted starts fully uninitialized."""
        self._reset_energy_ledger("uninitialized")
        if self._energy_ledger_store is None:
            return
        try:
            stored = await self._energy_ledger_store.async_load()
        except Exception as err:
            if isinstance(err, (KeyboardInterrupt, SystemExit)):
                raise
            _LOGGER.warning(
                "Unable to load energy ledger for %s: %s", self.entry.entry_id, err
            )
            return
        if stored is None:
            return
        if not isinstance(stored, dict):
            _LOGGER.warning(
                "Discarding malformed energy ledger for %s", self.entry.entry_id
            )
            self._ledger_status = "invalid_storage"
            return

        ledger = BatteryEnergyLedger.from_dict(stored.get("ledger"))
        observed_energy = stored.get("observed_energy_kwh")
        observed_at_raw = stored.get("observed_at")
        inventory = _coerce_ledger_inventory(stored.get("inventory"))
        observed_at = (
            dt_util.parse_datetime(observed_at_raw)
            if isinstance(observed_at_raw, str)
            else None
        )
        if (
            ledger is None
            or inventory is None
            or observed_at is None
            or not isinstance(observed_energy, (int, float))
            or isinstance(observed_energy, bool)
            or not isfinite(observed_energy)
            or observed_energy < 0
            # A ledger whose lots no longer sum to the recorded observation is
            # internally inconsistent and must not price an export.  The
            # allowance covers the pinning tolerance plus the rounding every lot
            # picked up on its way to disk, so writing a long-running ledger and
            # reading it straight back can never fail this check.
            or abs(ledger.total_kwh - float(observed_energy))
            > _ENERGY_LEDGER_TOLERANCE_KWH + ledger.storage_rounding_tolerance_kwh
        ):
            _LOGGER.warning(
                "Discarding untrusted energy ledger for %s", self.entry.entry_id
            )
            self._ledger_status = "invalid_storage"
            return

        self._energy_ledger = ledger
        self._ledger_last_energy_kwh = float(observed_energy)
        self._ledger_last_observed_at = dt_util.as_utc(observed_at)
        self._ledger_baseline_at = self._ledger_last_observed_at
        self._ledger_inventory = inventory
        self._ledger_initialized = True
        self._ledger_status = "restored"

    def _schedule_energy_ledger_save(self) -> None:
        """Queue a coalesced, atomic ledger write.

        The ledger changes on nearly every refresh, so an immediate write per
        cycle would hammer the flash storage.  ``async_delay_save`` keeps at
        most one pending write per delay window and Home Assistant flushes it
        on shutdown.
        """
        store = self._energy_ledger_store
        if store is None:
            return
        try:
            store.async_delay_save(
                self._energy_ledger_payload, _ENERGY_LEDGER_SAVE_DELAY_SECONDS
            )
        except Exception as err:
            if isinstance(err, (KeyboardInterrupt, SystemExit)):
                raise
            _LOGGER.warning(
                "Unable to persist energy ledger for %s: %s", self.entry.entry_id, err
            )

    @property
    def energy_ledger_inventory(self) -> tuple[tuple[str, float], ...] | None:
        """Return the battery fingerprint the current lots were accounted against."""
        return self._ledger_inventory

    def resolve_battery_energy_inventory(
        self, data: dict[str, Any]
    ) -> tuple[tuple[tuple[str, float], ...], float] | None:
        """Return the validated (inventory fingerprint, stored kWh) or None.

        Every consumer that reasons about *whose* energy is in the battery --
        the ledger and the arbitrage reserve floor -- must resolve the fleet
        through this one method, so a battery that drops out cannot shrink the
        reserve while the lots still account for the whole fleet.
        """
        return self._resolve_ledger_inventory(data)

    def _resolve_ledger_inventory(
        self, data: dict[str, Any]
    ) -> tuple[tuple[tuple[str, float], ...], float] | None:
        """Return the validated (inventory fingerprint, stored kWh) or None.

        Every configured battery must report a usable SOC and capacity.  A
        single dropout would otherwise look like an instantaneous discharge and
        silently consume lots that are still in the battery.
        """
        configured = self.config.get(CONF_BATTERY_SOC_ENTITIES) or []
        if not configured:
            return None
        details = data.get("battery_details")
        if not isinstance(details, list) or not details:
            return None

        observed: dict[str, tuple[float, float]] = {}
        for item in details:
            if not isinstance(item, dict):
                return None
            entity_id = item.get("entity_id")
            capacity = item.get("capacity")
            soc = item.get("soc")
            if (
                not isinstance(entity_id, str)
                or entity_id in observed
                or not isinstance(capacity, (int, float))
                or isinstance(capacity, bool)
                or not isinstance(soc, (int, float))
                or isinstance(soc, bool)
                or not isfinite(capacity)
                or not isfinite(soc)
                or capacity <= 0
                or not 0 <= soc <= 100
            ):
                return None
            observed[entity_id] = (float(capacity), float(soc))

        if set(observed) != set(configured):
            return None

        fingerprint = tuple(
            sorted(
                (entity_id, round(capacity, 6))
                for entity_id, (capacity, _soc) in observed.items()
            )
        )
        total = sum(capacity * soc / 100.0 for capacity, soc in observed.values())
        return fingerprint, total

    def _reconcile_energy_ledger(self, data: dict[str, Any]) -> None:
        """Attribute observed battery movement to virtual marginal-energy lots."""
        now = dt_util.utcnow()
        inventory = self._resolve_ledger_inventory(data)
        if inventory is None:
            # Incomplete or invalid battery data: leave the ledger untouched so
            # a sensor dropout can neither consume nor create lots.
            self._ledger_status = "battery_data_unavailable"
            return
        fingerprint, total = inventory

        if not self._ledger_initialized or fingerprint != self._ledger_inventory:
            # First trusted observation, or the SOC-to-kWh mapping changed.
            # Neither the existing lots nor the delta mean anything now.
            status = (
                "initialized" if not self._ledger_initialized else "inventory_changed"
            )
            self._energy_ledger.reset_to_unknown(total)
            self._ledger_inventory = fingerprint
            self._ledger_last_energy_kwh = total
            self._ledger_last_observed_at = now
            self._ledger_baseline_at = now
            self._ledger_initialized = True
            self._ledger_charge_intent = None
            self._ledger_status = status
            self._schedule_energy_ledger_save()
            return

        previous_observed_at = self._ledger_last_observed_at
        gap_seconds = (
            None
            if previous_observed_at is None
            else (now - previous_observed_at).total_seconds()
        )
        if (
            gap_seconds is None
            or gap_seconds < 0
            or gap_seconds > _ENERGY_LEDGER_MAX_ATTRIBUTION_GAP_SECONDS
        ):
            # Nothing was observed for too long.  The battery may have been
            # charged and discharged several times from several sources in the
            # meantime, so no existing lot survives -- not merely the delta.
            # Everything becomes unknown energy, which is never exported.
            self._energy_ledger.reset_to_unknown(total)
            self._ledger_last_energy_kwh = total
            self._ledger_last_observed_at = now
            self._ledger_baseline_at = now
            self._ledger_charge_intent = None
            self._ledger_status = "observation_gap"
            self._schedule_energy_ledger_save()
            return

        previous_total = self._ledger_last_energy_kwh or 0.0
        baseline_at = self._ledger_baseline_at
        elapsed_seconds = (
            None if baseline_at is None else (now - baseline_at).total_seconds()
        )
        delta = total - previous_total
        if abs(delta) <= _ENERGY_LEDGER_TOLERANCE_KWH:
            # Movement below SOC quantisation noise.  The energy baseline stays
            # anchored so slow drift accumulates into a single real movement
            # instead of triggering a write every refresh, but the observation
            # itself is accepted: an idle battery is watched, not stale.
            self._ledger_last_observed_at = now
            self._ledger_status = "steady"
            return

        if delta > 0:
            self._attribute_ledger_charge(delta, now, elapsed_seconds)
        else:
            self._energy_ledger.discharge(-delta)
            self._ledger_status = "discharged"

        # Keep the ledger pinned to the observed inventory.  Any residual drift
        # is unprovable energy, never sellable energy.
        drift = total - self._energy_ledger.total_kwh
        if drift > _ENERGY_LEDGER_TOLERANCE_KWH:
            self._energy_ledger.add(EnergyOrigin.UNKNOWN, drift)
        elif drift < -_ENERGY_LEDGER_TOLERANCE_KWH:
            self._energy_ledger.discharge(-drift)

        self._ledger_last_energy_kwh = total
        self._ledger_last_observed_at = now
        self._ledger_baseline_at = now
        self._schedule_energy_ledger_save()

    def _attribute_ledger_charge(
        self, delta_kwh: float, now: datetime, elapsed_seconds: float | None
    ) -> None:
        """Credit a charge delta to the recorded intent, capped by what it could deliver."""
        intent = self._ledger_charge_intent
        intent_age_seconds = (
            None if intent is None else (now - intent["at"]).total_seconds()
        )
        if (
            intent is None
            or intent_age_seconds is None
            or intent_age_seconds < 0
            or intent_age_seconds > _ENERGY_LEDGER_MAX_ATTRIBUTION_GAP_SECONDS
            or elapsed_seconds is None
            or elapsed_seconds <= 0
            or elapsed_seconds > _ENERGY_LEDGER_MAX_ATTRIBUTION_GAP_SECONDS
        ):
            # No trusted command covers this interval (restart, downtime or a
            # skipped refresh), so the energy has no provable origin.
            self._energy_ledger.add(EnergyOrigin.UNKNOWN, delta_kwh)
            self._ledger_status = "charged_unattributed"
            return

        # A source can only be credited for the energy it could physically have
        # delivered.  Two independent windows bound that: the time since the
        # energy baseline was set, and the age of the command itself.  The
        # command says nothing about what happened before it was issued, which
        # matters whenever sub-quantisation drift held the baseline back across
        # several refreshes.  Anything beyond the smaller window came from
        # somewhere else and is stacked on top as unknown, where it blocks the
        # attributed energy below it until it is discharged again.
        attributable_seconds = min(elapsed_seconds, intent_age_seconds)
        deliverable_kwh = (
            intent["power_w"]
            / 1000.0
            * (attributable_seconds / 3600.0)
            * _ENERGY_LEDGER_POWER_TOLERANCE
        )
        attributed = min(delta_kwh, max(0.0, deliverable_kwh))
        if attributed <= _ENERGY_LEDGER_TOLERANCE_KWH:
            self._energy_ledger.add(EnergyOrigin.UNKNOWN, delta_kwh)
            self._ledger_status = "charged_unattributed"
            return

        self._energy_ledger.add(intent["origin"], attributed, intent["buy_cost"])
        remainder = delta_kwh - attributed
        if remainder > _ENERGY_LEDGER_TOLERANCE_KWH:
            self._energy_ledger.add(EnergyOrigin.UNKNOWN, remainder)
            self._ledger_status = f"charged_{intent['origin'].value}_partial"
        else:
            self._ledger_status = f"charged_{intent['origin'].value}"

    def _ledger_all_in_buy_cost(self, price_analysis: dict[str, Any]) -> float | None:
        """Return the all-in import price per kWh, VAT included, or None.

        The lot price has to be what a kWh actually cost at the meter, because
        the export decision compares it against the feed-in price.  It is
        rebuilt from the raw price and the same adjustment, transport and VAT
        terms the buy timeline uses, rather than read from ``current_price``:
        that field carries VAT only on the interval-aware path, and a lot
        priced ex-VAT would authorise exports below break-even.
        """
        raw_price = price_analysis.get("raw_current_price")
        if (
            not isinstance(raw_price, (int, float))
            or isinstance(raw_price, bool)
            or not isfinite(raw_price)
        ):
            return None

        def _term(source: dict[str, Any], key: str, default: float) -> float | None:
            value = source.get(key, default)
            if value is None:
                value = default
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not isfinite(value)
            ):
                return None
            return float(value)

        multiplier = _term(
            price_analysis,
            "price_adjustment_multiplier",
            self.config.get(
                CONF_PRICE_ADJUSTMENT_MULTIPLIER, DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER
            ),
        )
        offset = _term(
            price_analysis,
            "price_adjustment_offset",
            self.config.get(
                CONF_PRICE_ADJUSTMENT_OFFSET, DEFAULT_PRICE_ADJUSTMENT_OFFSET
            ),
        )
        transport_cost = _term(price_analysis, "transport_cost", 0.0)
        buy_vat_multiplier = _term(
            self.config, CONF_BUY_VAT_MULTIPLIER, DEFAULT_BUY_VAT_MULTIPLIER
        )
        if (
            multiplier is None
            or offset is None
            or transport_cost is None
            or buy_vat_multiplier is None
        ):
            return None
        all_in = (float(raw_price) * multiplier + offset + transport_cost) * (
            buy_vat_multiplier
        )
        return all_in if isfinite(all_in) else None

    def _record_ledger_charge_intent(
        self,
        data: dict[str, Any],
        charging_decision: dict[str, Any],
        automatic_battery_grid_charging: bool,
        override_targets: set[str] | frozenset[str] | list[str],
    ) -> None:
        """Record what this cycle commanded, for the next observed SOC delta.

        Only a planner-issued command counts as proof of origin.  A manual
        battery override bypasses the planner entirely, so its energy stays
        unattributed rather than being credited to solar.
        """
        if "battery_grid_charging" in (override_targets or ()):
            self._ledger_charge_intent = None
            return

        now = dt_util.utcnow()
        commanded_grid_charging = bool(
            charging_decision.get("battery_grid_charging", False)
        )
        if automatic_battery_grid_charging or commanded_grid_charging:
            if not (automatic_battery_grid_charging and commanded_grid_charging):
                # The automatic decision and the command that will actually be
                # applied disagree, so neither proves what the battery is being
                # charged with.
                self._ledger_charge_intent = None
                return
            buy_cost = self._ledger_all_in_buy_cost(data.get("price_analysis") or {})
            if buy_cost is None:
                # Without a trusted all-in price the lot cannot be priced, and
                # an unpriced grid lot must never be sold.
                self._ledger_charge_intent = None
                return
            self._ledger_charge_intent = {
                "at": now,
                "origin": EnergyOrigin.GRID,
                # Negative all-in prices are kept with their sign.
                "buy_cost": buy_cost,
                "power_w": float(
                    self.config.get(CONF_MAX_BATTERY_POWER, DEFAULT_MAX_BATTERY_POWER)
                    or 0.0
                ),
            }
            return

        self._ledger_charge_intent = self._solar_charge_intent(
            data, charging_decision, now
        )

    def _solar_charge_intent(
        self,
        data: dict[str, Any],
        charging_decision: dict[str, Any],
        now: datetime,
    ) -> dict[str, Any] | None:
        """Return a solar intent only when solar is provably charging the battery.

        A solar allocation on its own is a plan, not a measurement: the planner
        never commands solar charging, the inverter does it with whatever is
        left after the house.  Solar is therefore credited only for the power
        the site is demonstrably not importing, so a battery filled from the
        grid can never be sold as solar.
        """
        if data.get("negative_buy_mode_active"):
            # Paid-to-consume import is grid energy by construction.
            return None

        allocation = charging_decision.get("power_allocation") or {}
        solar_for_batteries = allocation.get("solar_for_batteries")
        if (
            not isinstance(solar_for_batteries, (int, float))
            or isinstance(solar_for_batteries, bool)
            or not isfinite(solar_for_batteries)
            or solar_for_batteries <= 0
        ):
            return None

        grid_power = data.get("grid_power")
        if (
            not isinstance(grid_power, (int, float))
            or isinstance(grid_power, bool)
            or not isfinite(grid_power)
        ):
            # Without a grid reading the site could be importing everything the
            # battery is absorbing.  Unprovable energy stays unknown.
            return None

        # Any import means solar is already short of the house load, so only the
        # allocation the site is not importing can be solar.
        provable_solar_w = float(solar_for_batteries) - sensor_grid_import_w(
            float(grid_power)
        )
        if provable_solar_w <= 0:
            return None
        return {
            "at": now,
            "origin": EnergyOrigin.SOLAR,
            "buy_cost": None,
            # Solar credit is bounded by the allocation the planner actually
            # proved, so mixed grid/solar charging cannot be sold as solar.
            "power_w": provable_solar_w,
        }

    async def _async_load_battery_charging_state(self) -> None:
        """Restore battery and EV-feedback anti-flap state across restarts."""
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

        tracked_cap = stored.get("grid_tracking_charger_limit")
        if isinstance(tracked_cap, (int, float)) and tracked_cap >= 0:
            self._grid_tracking_charger_limit = int(tracked_cap)

        tracked_adjustment_raw = stored.get("grid_tracking_last_adjustment")
        if isinstance(tracked_adjustment_raw, str):
            parsed = dt_util.parse_datetime(tracked_adjustment_raw)
            if parsed is not None:
                self._grid_tracking_last_adjustment = dt_util.as_utc(parsed)
        # The physical-acknowledgement check compares measured EV power with
        # the cap that this process actually commanded.  After a restart no
        # command has been issued yet and the three-minute averaging window
        # starts empty, so a restored cap must not be treated as pending
        # application: doing so would hold the reduced cap through the whole
        # blind window and then consume the first fresh average as an "ack".
        self._grid_tracking_waiting_for_application = False
        self._grid_tracking_cap_restored_unvalidated = (
            self._grid_tracking_charger_limit is not None
        )

    async def _async_persist_battery_charging_state(self) -> None:
        """Persist battery and EV-feedback anti-flap state for restart recovery."""
        if self._battery_charging_state_store is None:
            return

        payload: dict[str, Any] = {
            "previous_battery_grid_charging": bool(
                self._previous_battery_grid_charging
            ),
            "battery_grid_charging_changed_at": (
                self._battery_grid_charging_changed_at.isoformat()
                if self._battery_grid_charging_changed_at is not None
                else None
            ),
            "battery_grid_charging_locked_threshold": (
                self._battery_grid_charging_locked_threshold
            ),
            "grid_tracking_charger_limit": self._grid_tracking_charger_limit,
            "grid_tracking_last_adjustment": (
                self._grid_tracking_last_adjustment.isoformat()
                if self._grid_tracking_last_adjustment is not None
                else None
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

    async def _resolve_solar_forecast(
        self, entity_id: str | None = None
    ) -> float | None:
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
        return bool(
            price_available and (price_range_available or price_analysis_available)
        )

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
        buy_vat_multiplier = self.config.get(
            CONF_BUY_VAT_MULTIPLIER, DEFAULT_BUY_VAT_MULTIPLIER
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
                    transport_cost = (
                        current_transport_cost
                        if current_transport_cost is not None
                        else 0.0
                    )
                # Apply VAT to the buy price (not feed-in)
                final_buy_price = (
                    adjusted_energy_price + transport_cost
                ) * buy_vat_multiplier

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
                        "final_price": final_buy_price,
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
            next_start = (
                intervals[idx + 1]["start"] if idx + 1 < len(intervals) else None
            )
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
        highest_today = (
            max(today_intervals, key=lambda item: item["final_price"])
            if today_intervals
            else None
        )
        lowest_today = (
            min(today_intervals, key=lambda item: item["final_price"])
            if today_intervals
            else None
        )

        if current_interval is None and highest_today is None and next_interval is None:
            return None

        return {
            "current_price": (
                current_interval["final_price"] if current_interval else None
            ),
            "highest_price": highest_today["final_price"] if highest_today else None,
            "lowest_price": lowest_today["final_price"] if lowest_today else None,
            "next_price": next_interval["final_price"] if next_interval else None,
            "raw_current_price": (
                current_interval["raw_price"] if current_interval else None
            ),
            "raw_highest_price": highest_today["raw_price"] if highest_today else None,
            "raw_lowest_price": lowest_today["raw_price"] if lowest_today else None,
            "raw_next_price": next_interval["raw_price"] if next_interval else None,
            "transport_cost": (
                current_interval["transport_cost"]
                if current_interval
                else current_transport_cost
            ),
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
        base_grid_setpoint = self.config.get(
            CONF_BASE_GRID_SETPOINT, DEFAULT_BASE_GRID_SETPOINT
        )
        try:
            monthly_peak_value = max(0.0, float(monthly_peak or 0))
        except (TypeError, ValueError):
            monthly_peak_value = 0.0
        if is_in_month_peak_transition_window(now=now):
            monthly_peak_value = 0.0
        effective_peak = max(monthly_peak_value, base_grid_setpoint)
        peak_threshold = (
            effective_peak * PEAK_THRESHOLD_MULTIPLIER if effective_peak > 0 else None
        )

        # Check current state
        grid_power = data.get("grid_power")
        car_power = data.get("car_charging_power") or 0.0
        min_car_threshold = self.config.get(
            CONF_MIN_CAR_CHARGING_THRESHOLD, DEFAULT_MIN_CAR_CHARGING_THRESHOLD
        )
        car_charging = car_power >= min_car_threshold
        currently_limited = bool(
            self._car_peak_limited_until and now < self._car_peak_limited_until
        )

        # Only monitor if car is charging, not already limited, and we have valid data
        if (
            peak_threshold
            and grid_power is not None
            and car_charging
            and not currently_limited
        ):
            # ``grid_power`` is the raw P1 sensor value.  Its opposite sign
            # from Planner setpoints is centralized in grid_signs.py.
            grid_import = sensor_grid_import_w(float(grid_power))

            if grid_import > peak_threshold:
                # Start or continue monitoring
                if self._car_peak_limit_started_at is None:
                    self._car_peak_limit_started_at = now
                    _LOGGER.debug(
                        "Grid import %.0fW > %.0fW: starting %d-minute monitoring",
                        grid_import,
                        peak_threshold,
                        PEAK_MONITORING_DURATION_MINUTES,
                    )
                else:
                    # Check if sustained for configured duration
                    exceed_duration = now - self._car_peak_limit_started_at
                    if exceed_duration >= timedelta(
                        minutes=PEAK_MONITORING_DURATION_MINUTES
                    ):
                        self._car_peak_limited_until = now + timedelta(
                            minutes=PEAK_LIMIT_DURATION_MINUTES
                        )
                        self._car_peak_limit_started_at = None  # Reset monitoring
                        _LOGGER.info(
                            "Grid import %.0fW exceeded %.0fW for %d minutes. "
                            "Halving car charger limit for %d minutes.",
                            grid_import,
                            peak_threshold,
                            PEAK_MONITORING_DURATION_MINUTES,
                            PEAK_LIMIT_DURATION_MINUTES,
                        )
                        currently_limited = True
            else:
                # Dropped below threshold - reset monitoring
                if self._car_peak_limit_started_at is not None:
                    _LOGGER.debug(
                        "Grid import back below threshold (%.0fW <= %.0fW) - monitoring reset",
                        grid_import,
                        peak_threshold,
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

        buy_vat_multiplier = self.config.get(
            CONF_BUY_VAT_MULTIPLIER, DEFAULT_BUY_VAT_MULTIPLIER
        )
        timeline = self._price_timeline_builder.build_purchase(
            prices_today,
            prices_tomorrow,
            transport_lookup,
            current_transport_cost,
            now,
            buy_vat_multiplier=buy_vat_multiplier,
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
            self.config.get(CONF_BUY_VAT_MULTIPLIER, DEFAULT_BUY_VAT_MULTIPLIER),
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
        tracked_entity_ids = self._tracked_entity_ids or set(
            self._collect_tracked_entity_ids()
        )
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
            if (
                self._last_entity_update is None
                or now - self._last_entity_update >= self._min_update_interval
            ):

                self._last_entity_update = now
                should_refresh = True
            else:
                time_remaining = (
                    self._last_entity_update + self._min_update_interval - now
                ).total_seconds()
                _LOGGER.debug(
                    "Entity update skipped for %s (throttled, %.1fs remaining)",
                    entity_id,
                    time_remaining,
                )

        if should_refresh:
            await self.async_request_refresh()
            _LOGGER.debug(
                "Entity update triggered for %s (throttled to %ds minimum)",
                entity_id,
                self._min_update_interval.total_seconds(),
            )

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

    def _update_battery_threshold_snapshot_if_needed(
        self, price_threshold: float | None, source: str = "configured"
    ) -> None:
        """Update battery threshold snapshot when entering a new price interval or config changes.

        The snapshot is also refreshed when the threshold *source* changes
        (configured fallback <-> rolling average).  Otherwise a fallback value
        captured while the average was briefly unavailable (e.g. the first
        cycle after a restart) stays pinned for the rest of the interval even
        though the decision-visible average threshold is already stricter.
        """
        if price_threshold is None:
            return

        current_interval = self._get_current_price_interval_start()

        # Calculate config hash to detect changes
        config_hash = hash(
            (
                self.config.get(CONF_PRICE_THRESHOLD),
                self.config.get(CONF_USE_AVERAGE_THRESHOLD),
                self.config.get(CONF_PRICE_ADJUSTMENT_MULTIPLIER),
                self.config.get(CONF_PRICE_ADJUSTMENT_OFFSET),
            )
        )

        # Update if:
        # 1. First interval
        # 2. New interval started
        # 3. Configuration changed (force update even mid-interval)
        config_changed = (
            self._last_config_hash is not None and config_hash != self._last_config_hash
        )

        source_changed = (
            self._battery_threshold_snapshot_source is not None
            and source != self._battery_threshold_snapshot_source
        )

        if (
            self._current_price_interval_start is None
            or current_interval != self._current_price_interval_start
            or config_changed
            or source_changed
        ):

            self._current_price_interval_start = current_interval
            self._battery_threshold_snapshot = price_threshold
            self._battery_threshold_snapshot_source = source
            self._last_config_hash = config_hash

            if config_changed:
                reason = "config changed"
            elif source_changed:
                reason = f"threshold source changed to {source}"
            else:
                reason = "new interval"
            _LOGGER.debug(
                "Battery threshold snapshot updated (%s) at %s: %.4f€/kWh",
                reason,
                current_interval.strftime("%H:%M"),
                price_threshold,
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
            average_threshold_active = self._should_use_average_threshold(
                average_threshold
            )
            data["average_threshold_active"] = average_threshold_active
            data["average_threshold_candidate"] = average_threshold
            if not average_threshold_active:
                data["average_threshold"] = None

            if average_threshold_active:
                current_threshold = average_threshold
            else:
                current_threshold = self.config.get(
                    CONF_PRICE_THRESHOLD, DEFAULT_PRICE_THRESHOLD
                )

            # Update battery threshold snapshot if we've entered a new 15-min interval
            self._update_battery_threshold_snapshot_if_needed(
                current_threshold,
                "average" if average_threshold_active else "configured",
            )

            # Add previous car charging state for hysteresis logic
            data["previous_car_charging"] = self._previous_car_charging
            data["previous_battery_grid_charging"] = (
                self._previous_battery_grid_charging
            )
            # Hide a restart-restored cap from the decision engine until this
            # process has measured a complete P1 window of its own.  Exposing
            # it earlier would clamp the charger for up to three minutes on
            # evidence that predates the restart.
            if (
                self._grid_tracking_cap_restored_unvalidated
                and data.get("grid_tracking_average_power") is None
            ):
                data["grid_tracking_charger_limit"] = None
                data["grid_tracking_last_adjustment"] = None
                data["grid_tracking_waiting_for_application"] = False
            else:
                data["grid_tracking_charger_limit"] = self._grid_tracking_charger_limit
                data["grid_tracking_last_adjustment"] = (
                    self._grid_tracking_last_adjustment
                )
                data["grid_tracking_waiting_for_application"] = (
                    self._grid_tracking_waiting_for_application
                )
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

            self._reconcile_energy_ledger(data)
            data["energy_ledger"] = self.energy_ledger_diagnostics()

            arbitrage_mode_plan = self._calculate_arbitrage_mode_plan(data)
            data["arbitrage_mode_plan"] = arbitrage_mode_plan
            data["arbitrage_mode_enabled"] = arbitrage_mode_plan.get("enabled", False)
            data["arbitrage_mode_active"] = arbitrage_mode_plan.get("active", False)
            data["arbitrage_mode_reason"] = arbitrage_mode_plan.get("reason")
            data["arbitrage_mode_reserve_soc"] = arbitrage_mode_plan.get("reserve_soc")
            data["arbitrage_mode_export_power"] = arbitrage_mode_plan.get(
                "export_power", 0
            )

            negative_buy_plan = self._calculate_negative_buy_plan(data)
            data["negative_buy_plan"] = negative_buy_plan
            data["negative_buy_mode_enabled"] = negative_buy_plan.get("enabled", False)
            data["negative_buy_mode_active"] = negative_buy_plan.get("active", False)
            data["negative_buy_mode_reason"] = negative_buy_plan.get("reason")
            data["negative_buy_import_power"] = negative_buy_plan.get("import_power", 0)
            data["negative_buy_curtail_solar"] = negative_buy_plan.get(
                "solar_curtail_active", False
            )

            charging_decision = await self.decision_engine.evaluate_charging_decision(
                data
            )
            automatic_battery_grid_charging = bool(
                charging_decision.get("battery_grid_charging", False)
            )
            automatic_effective_threshold = charging_decision.get(
                "battery_effective_threshold"
            )
            charging_decision, override_targets = self._apply_manual_overrides(
                charging_decision
            )

            if override_targets:
                charging_decision = self.decision_engine.recalculate_after_override(
                    data, charging_decision, override_targets
                )
                if "charger_limit" in override_targets:
                    # A manual EV cap is authoritative.  Do not leave a
                    # stale automatic feedback cap behind it.
                    charging_decision["grid_tracking_charger_limit"] = None
                    charging_decision["grid_tracking_last_adjustment"] = None
                    charging_decision["grid_tracking_waiting_for_application"] = False

            data.update(charging_decision)
            # Snapshot what this cycle actually commanded.  It is consumed by
            # the next observed SOC delta and never persisted.
            self._record_ledger_charge_intent(
                data,
                charging_decision,
                automatic_battery_grid_charging,
                override_targets,
            )
            previous_grid_tracking_cap = self._grid_tracking_charger_limit
            previous_grid_tracking_adjustment = self._grid_tracking_last_adjustment
            previous_grid_tracking_waiting = self._grid_tracking_waiting_for_application
            self._grid_tracking_charger_limit = charging_decision.get(
                "grid_tracking_charger_limit"
            )
            self._grid_tracking_last_adjustment = charging_decision.get(
                "grid_tracking_last_adjustment"
            )
            self._grid_tracking_waiting_for_application = bool(
                charging_decision.get("grid_tracking_waiting_for_application", False)
            )

            # Update feedback state only when the explicitly opt-in P1
            # feedback feature is enabled.  The default configuration must
            # retain the existing coordinator behaviour exactly.
            car_grid_charging = bool(charging_decision.get("car_grid_charging", False))
            grid_tracking_enabled = bool(
                self.config.get(CONF_GRID_IMPORT_NEGATIVE_FEEDBACK, False)
            )
            if data.get("grid_tracking_average_power") is not None:
                # A complete window measured by this process supersedes any
                # value restored from disk.
                self._grid_tracking_cap_restored_unvalidated = False
            if not grid_tracking_enabled or not car_grid_charging:
                # An explicit Off ends the feedback session immediately.
                # The next On starts a completely fresh P1 averaging window.
                self._grid_tracking_charger_limit = None
                self._grid_tracking_last_adjustment = None
                self._grid_tracking_waiting_for_application = False
                self._grid_tracking_cap_restored_unvalidated = False
                self._grid_tracking_power_history.clear()
                self._grid_tracking_window_started_at = None
            elif not self._previous_car_charging:
                self._grid_tracking_power_history.clear()
                tracking_started_at = dt_util.utcnow()
                self._grid_tracking_window_started_at = tracking_started_at
                # Use the P1 reading gathered for this same evaluation as the
                # first sample of the new session.  Waiting for the next
                # refresh would make a nominal three-minute window shorter
                # whenever Home Assistant updates are delayed.
                if data.get("grid_power") is not None:
                    self._grid_tracking_power_history.append(
                        (tracking_started_at, float(data["grid_power"]))
                    )
            elif charging_decision.get("grid_tracking_limit_acknowledged"):
                acknowledged_at = dt_util.utcnow()
                self._grid_tracking_power_history.clear()
                self._grid_tracking_window_started_at = acknowledged_at
                if data.get("grid_power") is not None:
                    self._grid_tracking_power_history.append(
                        (acknowledged_at, float(data["grid_power"]))
                    )
            self._previous_car_charging = car_grid_charging
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
                or self._battery_grid_charging_locked_threshold
                != previous_locked_threshold
                or self._grid_tracking_charger_limit != previous_grid_tracking_cap
                or self._grid_tracking_last_adjustment
                != previous_grid_tracking_adjustment
                or self._grid_tracking_waiting_for_application
                != previous_grid_tracking_waiting
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
            _LOGGER.debug(
                "Battery entity %s: state=%s, parsed_value=%s",
                entity_id,
                state.state if state else "missing",
                soc,
            )
            if soc is not None:
                # Validate and normalize battery SOC
                if 0 <= soc <= BATTERY_SOC_DECIMAL_THRESHOLD:
                    # SOC appears to be in decimal format (0-1), convert to percentage
                    _LOGGER.info(
                        "Battery SOC for %s appears to be decimal (%.3f), converting to percentage (%.1f%%)",
                        entity_id,
                        soc,
                        soc * 100,
                    )
                    soc = soc * 100
                elif not 0 <= soc <= 100:
                    # SOC is outside valid range
                    _LOGGER.error(
                        "Invalid battery SOC value for %s: %.2f (expected 0-100%%), excluding from calculations",
                        entity_id,
                        soc,
                    )
                    battery_soc_values.append({"entity_id": entity_id, "soc": None})
                    continue

                battery_soc_values.append({"entity_id": entity_id, "soc": soc})
            else:
                # Emit a placeholder rather than dropping the battery outright.
                # BatteryAnalysisCalculator can then substitute this battery's
                # last valid reading for a short while, so a transient sensor
                # dropout does not silently re-weight the fleet average onto the
                # remaining batteries and flip charging decisions.
                _LOGGER.warning(
                    "Battery entity %s is unavailable - holding last known SOC if recent",
                    entity_id,
                )
                battery_soc_values.append({"entity_id": entity_id, "soc": None})

        # Substitute recently-held readings for momentarily unavailable sensors so
        # battery_details/phase_batteries and the decision engine agree on SOC.
        battery_soc_values = self.decision_engine.resolve_held_battery_soc(
            battery_soc_values
        )

        data["battery_soc"] = battery_soc_values
        _LOGGER.debug("Final battery SOC data: %s", battery_soc_values)

        # Map batteries to phases (always available for diagnostics)
        battery_capacities_cfg = self.config.get(CONF_BATTERY_CAPACITIES, {})
        phase_capacity_map: dict[str, float] = {phase_id: 0.0 for phase_id in PHASE_IDS}
        phase_batteries: dict[str, list[dict[str, Any]]] = {
            phase_id: [] for phase_id in PHASE_IDS
        }
        battery_details: list[dict[str, Any]] = []

        default_phase = PHASE_IDS[0]
        for battery in battery_soc_values:
            entity_id = battery["entity_id"]
            assigned_phases = self.battery_phase_assignments.get(entity_id)
            if assigned_phases:
                valid_phases = [
                    phase for phase in assigned_phases if phase in PHASE_IDS
                ]
                assigned_phases = valid_phases or [default_phase]
            else:
                assigned_phases = [default_phase]

            capacity = battery_capacities_cfg.get(entity_id)
            if capacity is None or capacity <= 0:
                _LOGGER.warning(
                    "Battery capacity not configured or invalid for %s - using fallback weight %.1f kWh. "
                    "Configure capacity in integration options for accurate weighted SOC calculations.",
                    entity_id,
                    BATTERY_CAPACITY_FALLBACK_WEIGHT,
                )
                capacity = BATTERY_CAPACITY_FALLBACK_WEIGHT  # fallback weight when capacity not provided

            battery_entry = {
                "entity_id": entity_id,
                "soc": battery["soc"],
                "capacity": capacity,
                "phases": assigned_phases,
            }
            battery_details.append(battery_entry)

            phase_share = (
                (capacity or 0) / len(assigned_phases) if assigned_phases else 0
            )
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
            data["house_consumption"] = (
                total_consumption_value if consumption_present else None
            )

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
        data["previous_grid_power"] = self.data.get("grid_power") if self.data else None

        evaluated_at = dt_util.utcnow()
        if data["grid_power"] is not None:
            self._inverter_derating_grid_power_history.append(
                (evaluated_at, float(data["grid_power"]))
            )
        cutoff = evaluated_at - _INVERTER_DERATING_EXPORT_AVERAGE_WINDOW
        while (
            self._inverter_derating_grid_power_history
            and self._inverter_derating_grid_power_history[0][0] < cutoff
        ):
            self._inverter_derating_grid_power_history.popleft()
        data["inverter_derating_grid_power_history"] = [
            {"timestamp": timestamp, "grid_power": grid_power}
            for timestamp, grid_power in self._inverter_derating_grid_power_history
        ]
        # EV feedback is deliberately independent from inverter derating.  It
        # only collects samples while a car-charging decision remains On, and
        # exposes no average until a complete new window has elapsed.
        grid_tracking_enabled = bool(
            self.config.get(CONF_GRID_IMPORT_NEGATIVE_FEEDBACK, False)
        )
        if (
            grid_tracking_enabled
            and self._grid_tracking_window_started_at is not None
            and data["grid_power"] is not None
        ):
            self._grid_tracking_power_history.append(
                (evaluated_at, float(data["grid_power"]))
            )
        grid_tracking_cutoff = evaluated_at - _GRID_TRACKING_AVERAGE_WINDOW
        # Keep the final sample just before the window so its value can be
        # weighted from the exact window boundary to the next P1 update.
        while (
            len(self._grid_tracking_power_history) > 1
            and self._grid_tracking_power_history[1][0] <= grid_tracking_cutoff
        ):
            self._grid_tracking_power_history.popleft()

        if (
            grid_tracking_enabled
            and self._grid_tracking_window_started_at is not None
            and evaluated_at - self._grid_tracking_window_started_at
            >= _GRID_TRACKING_AVERAGE_WINDOW
            and self._grid_tracking_power_history
        ):
            data["grid_tracking_average_power"] = self._time_weighted_power_average(
                self._grid_tracking_power_history,
                max(self._grid_tracking_window_started_at, grid_tracking_cutoff),
                evaluated_at,
            )
        else:
            data["grid_tracking_average_power"] = None

        # Preserve prior inverter target so the derating controller can hold or
        # release curtailment gradually instead of jumping back to max power.
        data["previous_inverter_derating_target"] = (
            self.data.get("inverter_derating_target") if self.data else None
        )
        data["previous_inverter_derating_unreached_since"] = (
            self.data.get("inverter_derating_unreached_since") if self.data else None
        )
        data["inverter_derating_evaluated_at"] = evaluated_at

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
        data["car_charging_locked_threshold"] = (
            self.data.get("car_charging_locked_threshold") if self.data else None
        )

        # Expose car permissive mode state in data dict (source of truth is self._car_permissive_mode_active)
        data["car_permissive_mode_active"] = self._car_permissive_mode_active

        # Transport cost: use built-in components if configured, otherwise legacy entity
        if self._has_builtin_transport_cost():
            # Built-in mode: no external entity or history lookup needed
            data["transport_cost"] = self._resolve_builtin_transport_cost(
                dt_util.utcnow()
            )
            data["transport_cost_lookup"] = []
            data["transport_cost_status"] = "builtin"
            # Store current P1 tariff code for diagnostics
            p1_entity = self.config.get(CONF_P1_TARIFF_ENTITY)
            if p1_entity:
                state = self.hass.states.get(p1_entity)
                data["p1_tariff_code"] = (
                    state.state
                    if state and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)
                    else None
                )
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
            data["nordpool_prices_today"] = await self._fetch_nordpool_prices(
                nordpool_config_entry, "today"
            )
            data["nordpool_prices_tomorrow"] = await self._fetch_nordpool_prices(
                nordpool_config_entry, "tomorrow"
            )
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
            transport_lookup,
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

        data["battery_forecast_prices"] = self._battery_forecast_prices(
            data["forecast_summary"]
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

    async def _fetch_nordpool_prices(
        self, config_entry_id: str, day: str
    ) -> dict[str, Any] | None:
        """Delegate to the Nord Pool service collaborator."""
        return await self._nordpool_service.fetch_prices(config_entry_id, day)

    def _calculate_average_threshold(
        self,
        prices_today: dict[str, Any] | None,
        prices_tomorrow: dict[str, Any] | None,
        transport_lookup: list[dict[str, Any]] | None,
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

    def _battery_forecast_prices(
        self, forecast_summary: dict[str, Any] | None
    ) -> list[tuple[datetime, datetime, float]]:
        """Return fresh, future purchase-price segments for battery deferral.

        Only exported when the forecast summary was built from current Nord
        Pool data this cycle: summary available and not stale, timeline
        generated within ``PRICE_TIMELINE_MAX_AGE_HOURS``, and the timeline
        contains the interval covering *now* (rejects day-old data that still
        hashes identically).  An empty list means "no usable forecast" and the
        battery deferral guard then never defers.
        """
        if not isinstance(forecast_summary, dict):
            return []
        if not forecast_summary.get("available") or forecast_summary.get("stale"):
            return []
        timeline = self._last_price_timeline
        generated_at = self._last_price_timeline_generated_at
        if not timeline or generated_at is None:
            return []
        now = dt_util.utcnow()
        age = now - generated_at
        if age < timedelta(0) or age > self._price_timeline_max_age:
            return []
        if not any(
            start is not None and end is not None and start <= now < end
            for start, end, _price in timeline
        ):
            return []
        return [
            (start, end, float(price))
            for start, end, price in timeline
            if end is not None and end > now and price is not None
        ]

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
                    "electricity_planner_data_restored",
                )
                _LOGGER.info(
                    "Data availability restored after %.1f seconds",
                    unavailable_duration.total_seconds(),
                )
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
                if (
                    unavailable_duration > timedelta(minutes=1)
                    and not self._notification_sent
                ):
                    await self._send_notification(
                        "Electricity Planner Data Unavailable",
                        f"Critical data (Nord Pool prices) has been unavailable for {unavailable_duration.total_seconds():.0f} seconds. "
                        f"All charging from grid is disabled for safety. Please check your Nord Pool integration.",
                        "electricity_planner_data_unavailable",
                    )
                    self._notification_sent = True
                    _LOGGER.error(
                        "Data unavailable notification sent after %.1f seconds",
                        unavailable_duration.total_seconds(),
                    )

    async def _send_notification(
        self, title: str, message: str, notification_id: str
    ) -> None:
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
