"""Decision engine for electricity planning."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, TypedDict

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .battery_analysis import BatteryAnalysisCalculator
from .battery_charging import BatteryChargingDecisionCalculator
from .car_charging import CarChargingDecisionCalculator
from .charger_limit import ChargerLimitCalculator
from .const import (
    CONF_ARBITRAGE_MODE_RESERVE_SOC,
    CONF_ARBITRAGE_MODE_RESERVE_SOC_SUNNY,
    CONF_BASE_GRID_SETPOINT,
    CONF_BATTERY_CAPACITIES,
    CONF_BUY_VAT_MULTIPLIER,
    CONF_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
    CONF_CAR_USE_BATTERY_ARBITRAGE,
    CONF_DYNAMIC_THRESHOLD_CONFIDENCE,
    CONF_EMERGENCY_SOC_THRESHOLD,
    CONF_FEEDIN_ADJUSTMENT_MULTIPLIER,
    CONF_FEEDIN_ADJUSTMENT_OFFSET,
    CONF_FEEDIN_PRICE_THRESHOLD,
    CONF_INVERTER_DERATING_SOC_BYPASS_THRESHOLD,
    CONF_INVERTER_DERATING_UNUSED_RELEASE_MINUTES,
    CONF_INVERTER_EXPORT_DEADBAND,
    CONF_INVERTER_EXPORT_LIMIT,
    CONF_MAX_BATTERY_POWER,
    CONF_MAX_CAR_POWER,
    CONF_MAX_GRID_POWER,
    CONF_MAX_INVERTER_POWER,
    CONF_MAX_SOC_THRESHOLD,
    CONF_MAX_SOC_THRESHOLD_SOLAR,
    CONF_MAX_SOC_THRESHOLD_SUNNY,
    CONF_MIN_CAR_CHARGING_DURATION,
    CONF_MIN_CAR_CHARGING_THRESHOLD,
    CONF_MIN_SOC_THRESHOLD,
    CONF_PHASE_MODE,
    CONF_PREDICTIVE_CHARGING_MIN_SOC,
    CONF_PRICE_ADJUSTMENT_MULTIPLIER,
    CONF_PRICE_ADJUSTMENT_OFFSET,
    CONF_PRICE_THRESHOLD,
    CONF_SIGNIFICANT_SOLAR_THRESHOLD,
    CONF_SOC_BUFFER_TARGET,
    CONF_SOC_PRICE_MULTIPLIER_MAX,
    CONF_SUNNY_FORECAST_THRESHOLD_KWH,
    CONF_USE_AVERAGE_THRESHOLD,
    CONF_USE_DYNAMIC_THRESHOLD,
    CONF_VERY_LOW_PRICE_THRESHOLD,
    DEFAULT_ARBITRAGE_MODE_RESERVE_SOC,
    DEFAULT_ARBITRAGE_MODE_RESERVE_SOC_SUNNY,
    DEFAULT_BASE_GRID_SETPOINT,
    DEFAULT_BUY_VAT_MULTIPLIER,
    DEFAULT_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
    DEFAULT_CAR_USE_BATTERY_ARBITRAGE,
    DEFAULT_DYNAMIC_THRESHOLD_CONFIDENCE,
    DEFAULT_EMERGENCY_SOC,
    DEFAULT_FEEDIN_ADJUSTMENT_MULTIPLIER,
    DEFAULT_FEEDIN_ADJUSTMENT_OFFSET,
    DEFAULT_FEEDIN_PRICE_THRESHOLD,
    DEFAULT_INVERTER_DERATING_SOC_BYPASS_THRESHOLD,
    DEFAULT_INVERTER_DERATING_UNUSED_RELEASE_MINUTES,
    DEFAULT_INVERTER_EXPORT_DEADBAND,
    DEFAULT_INVERTER_EXPORT_LIMIT,
    DEFAULT_MAX_BATTERY_POWER,
    DEFAULT_MAX_CAR_POWER,
    DEFAULT_MAX_GRID_POWER,
    DEFAULT_MAX_INVERTER_POWER,
    DEFAULT_MAX_SOC,
    DEFAULT_MAX_SOC_SOLAR,
    DEFAULT_MAX_SOC_SUNNY,
    DEFAULT_MIN_CAR_CHARGING_DURATION,
    DEFAULT_MIN_CAR_CHARGING_THRESHOLD,
    DEFAULT_MIN_SOC,
    DEFAULT_PREDICTIVE_CHARGING_MIN_SOC,
    DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER,
    DEFAULT_PRICE_ADJUSTMENT_OFFSET,
    DEFAULT_PRICE_THRESHOLD,
    DEFAULT_SIGNIFICANT_SOLAR_THRESHOLD,
    DEFAULT_SOC_BUFFER_TARGET,
    DEFAULT_SOC_PRICE_MULTIPLIER_MAX,
    DEFAULT_SUNNY_FORECAST_THRESHOLD_KWH,
    DEFAULT_USE_AVERAGE_THRESHOLD,
    DEFAULT_USE_DYNAMIC_THRESHOLD,
    DEFAULT_VERY_LOW_PRICE_THRESHOLD,
    MAX_CAR_POWER_VALIDATION_W,
    MAX_POWER_VALIDATION_W,
    PERMISSIVE_MULTIPLIER_MAX,
    PERMISSIVE_MULTIPLIER_MIN,
    PHASE_MODE_SINGLE,
    PHASE_MODE_THREE,
)
from .defaults import (
    DEFAULT_POWER_ESTIMATES,
    DEFAULT_SYSTEM_LIMITS,
    calculate_soc_price_multiplier,
)
from .feedin_decision import FeedInDecisionCalculator
from .grid_setpoint import GridSetpointCalculator
from .helpers import (
    DataValidator,
    PowerAllocationValidator,
    PriceCalculator,
    TimeContext,
)
from .inverter_derating import InverterDeratingCalculator
from .override_recalculator import OverrideRecalculator
from .phase_distributor import PhaseDistributor
from .price_analysis import PriceAnalysisCalculator
from .solar_allocation import SolarAllocationCalculator
from .strategies import StrategyManager

_LOGGER = logging.getLogger(__name__)

# Internal state keys
_CAR_CHARGING_LOCKED_THRESHOLD_KEY = "car_charging_locked_threshold"
_PREVIOUS_CAR_CHARGING_KEY = "previous_car_charging"
_HAS_MIN_CHARGING_WINDOW_KEY = "has_min_charging_window"
_PREVIOUS_BATTERY_GRID_CHARGING_KEY = "previous_battery_grid_charging"
_BATTERY_GRID_CHARGING_STATE_AGE_SECONDS_KEY = "battery_grid_charging_state_age_seconds"
_BATTERY_GRID_CHARGING_LOCKED_THRESHOLD_KEY = "battery_grid_charging_locked_threshold"
_SUNNY_DAY_GRID_SOC_HYSTERESIS_PERCENT = 2.0


class CarChargingDecision(TypedDict, total=False):
    """Type definition for car charging decision results."""

    car_grid_charging: bool
    car_grid_import_allowed: bool
    car_grid_charging_reason: str
    car_solar_only: bool


def _safe_optional_float(value: Any) -> float | None:
    """Best-effort conversion of arbitrary input to float."""
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _safe_optional_datetime(value: Any) -> datetime | None:
    """Best-effort conversion of arbitrary input to an aware datetime."""
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value
        return value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        parsed = dt_util.parse_datetime(value)
        if parsed is None:
            return None
        if parsed.tzinfo is not None:
            return parsed
        return parsed.replace(tzinfo=timezone.utc)
    return None


@dataclass
class ConfigExtractor:
    """Helper to extract and validate configuration values."""

    config: dict[str, Any]
    validator: DataValidator

    def get_power_setting(
        self,
        key: str,
        default: int,
        min_val: int,
        max_val: int,
    ) -> int:
        """Extract and validate a power setting (W)."""
        return int(
            self.validator.sanitize_config_value(
                self.config.get(key, default),
                min_val=min_val,
                max_val=max_val,
                default=default,
                name=key,
            )
        )

    def get_float(
        self,
        key: str,
        default: float,
        name: str | None = None,
    ) -> float:
        """Extract and coerce a float value."""
        value = self.config.get(key, default)
        try:
            return float(value)
        except (TypeError, ValueError):
            _LOGGER.warning(
                "%s value %s invalid, using default %s", name or key, value, default
            )
            return float(default)

    def get_int(
        self,
        key: str,
        default: int,
        name: str | None = None,
        minimum: int | None = None,
        maximum: int | None = None,
    ) -> int:
        """Extract and coerce an int value with optional bounds."""
        coerced = self.get_float(key, default, name or key)
        if minimum is not None and coerced < minimum:
            _LOGGER.warning(
                "%s value %s below minimum %s, using %s",
                name or key,
                coerced,
                minimum,
                default,
            )
            return int(default)
        if maximum is not None and coerced > maximum:
            _LOGGER.warning(
                "%s value %s above maximum %s, using %s",
                name or key,
                coerced,
                maximum,
                default,
            )
            return int(default)
        return int(coerced)

    def get_bool(self, key: str, default: bool) -> bool:
        """Extract a boolean value."""
        return bool(self.config.get(key, default))


@dataclass(frozen=True)
class EngineSettings:
    """Sanitized configuration values used by the decision engine."""

    max_grid_power: int
    base_grid_setpoint: int
    max_inverter_power: int
    inverter_export_limit: int
    inverter_export_deadband: int
    inverter_derating_unused_release_minutes: int
    inverter_derating_soc_bypass_threshold: float
    max_battery_power: int
    max_car_power: int
    min_car_charging_threshold: int
    min_car_charging_duration: int
    car_permissive_threshold_multiplier: float
    car_use_battery_arbitrage: bool
    price_adjustment_multiplier: float
    price_adjustment_offset: float
    feedin_adjustment_multiplier: float
    feedin_adjustment_offset: float
    buy_vat_multiplier: float
    price_threshold: float
    very_low_price_threshold_ratio: float
    very_low_price_threshold_pct: float
    feedin_threshold: float
    significant_solar_threshold: int
    min_soc_threshold: float
    max_soc_threshold: float
    arbitrage_mode_reserve_soc: float
    arbitrage_mode_reserve_soc_sunny: float
    max_soc_threshold_sunny: float
    max_soc_threshold_solar: float
    sunny_forecast_threshold_kwh: float
    emergency_soc_threshold: float
    predictive_min_soc: float
    use_dynamic_threshold: bool
    dynamic_threshold_confidence: float
    use_average_threshold: bool
    soc_price_multiplier_max: float
    soc_buffer_target: float
    battery_capacities: dict[str, float]

    @classmethod
    def from_config(
        cls, config: dict[str, Any], validator: DataValidator
    ) -> "EngineSettings":
        """Create sanitized settings from raw configuration."""
        extractor = ConfigExtractor(config, validator)

        # Extract battery capacities
        battery_capacity_config = config.get(CONF_BATTERY_CAPACITIES, {}) or {}
        sanitized_capacities: dict[str, float] = {}
        for entity_id, raw_capacity in battery_capacity_config.items():
            try:
                capacity = float(raw_capacity)
            except (TypeError, ValueError):
                _LOGGER.warning(
                    "Battery capacity for %s invalid (%s) - skipping",
                    entity_id,
                    raw_capacity,
                )
                continue
            if capacity <= 0:
                _LOGGER.warning(
                    "Battery capacity for %s non-positive (%s) - skipping",
                    entity_id,
                    capacity,
                )
                continue
            sanitized_capacities[entity_id] = capacity

        # Extract power settings
        max_grid_power = extractor.get_power_setting(
            CONF_MAX_GRID_POWER, DEFAULT_MAX_GRID_POWER, 1000, MAX_POWER_VALIDATION_W
        )
        base_grid_setpoint = extractor.get_power_setting(
            CONF_BASE_GRID_SETPOINT, DEFAULT_BASE_GRID_SETPOINT, 1000, 15000
        )
        max_inverter_power = extractor.get_power_setting(
            CONF_MAX_INVERTER_POWER,
            DEFAULT_MAX_INVERTER_POWER,
            0,
            MAX_POWER_VALIDATION_W,
        )
        inverter_export_limit = extractor.get_power_setting(
            CONF_INVERTER_EXPORT_LIMIT, DEFAULT_INVERTER_EXPORT_LIMIT, 0, 5000
        )
        inverter_export_deadband = extractor.get_power_setting(
            CONF_INVERTER_EXPORT_DEADBAND, DEFAULT_INVERTER_EXPORT_DEADBAND, 0, 1000
        )
        inverter_derating_unused_release_minutes = extractor.get_int(
            CONF_INVERTER_DERATING_UNUSED_RELEASE_MINUTES,
            DEFAULT_INVERTER_DERATING_UNUSED_RELEASE_MINUTES,
            "inverter_derating_unused_release_minutes",
            minimum=0,
            maximum=120,
        )
        inverter_derating_soc_bypass_threshold = extractor.get_float(
            CONF_INVERTER_DERATING_SOC_BYPASS_THRESHOLD,
            DEFAULT_INVERTER_DERATING_SOC_BYPASS_THRESHOLD,
            "inverter_derating_soc_bypass_threshold",
        )
        inverter_derating_soc_bypass_threshold = max(
            0.0, min(inverter_derating_soc_bypass_threshold, 100.0)
        )
        max_battery_power = extractor.get_power_setting(
            CONF_MAX_BATTERY_POWER,
            DEFAULT_MAX_BATTERY_POWER,
            500,
            MAX_POWER_VALIDATION_W,
        )
        max_car_power = extractor.get_power_setting(
            CONF_MAX_CAR_POWER,
            DEFAULT_MAX_CAR_POWER,
            500,
            DEFAULT_SYSTEM_LIMITS.max_car_charger_power * 2,
        )
        min_car_threshold = extractor.get_power_setting(
            CONF_MIN_CAR_CHARGING_THRESHOLD,
            DEFAULT_MIN_CAR_CHARGING_THRESHOLD,
            0,
            max_car_power,
        )
        significant_solar_threshold = extractor.get_power_setting(
            CONF_SIGNIFICANT_SOLAR_THRESHOLD,
            DEFAULT_SIGNIFICANT_SOLAR_THRESHOLD,
            0,
            MAX_POWER_VALIDATION_W,
        )

        # Extract durations and counts
        min_car_duration = extractor.get_int(
            CONF_MIN_CAR_CHARGING_DURATION,
            DEFAULT_MIN_CAR_CHARGING_DURATION,
            "min_car_charging_duration",
            minimum=0,
            maximum=24,
        )

        # Extract car permissive mode multiplier
        car_permissive_multiplier = extractor.get_float(
            CONF_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
            DEFAULT_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
            "car_permissive_threshold_multiplier",
        )
        car_use_battery_arbitrage = extractor.get_bool(
            CONF_CAR_USE_BATTERY_ARBITRAGE, DEFAULT_CAR_USE_BATTERY_ARBITRAGE
        )

        # Extract price adjustments
        price_multiplier = extractor.get_float(
            CONF_PRICE_ADJUSTMENT_MULTIPLIER,
            DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER,
            "price_adjustment_multiplier",
        )
        price_offset = extractor.get_float(
            CONF_PRICE_ADJUSTMENT_OFFSET,
            DEFAULT_PRICE_ADJUSTMENT_OFFSET,
            "price_adjustment_offset",
        )
        feed_multiplier = extractor.get_float(
            CONF_FEEDIN_ADJUSTMENT_MULTIPLIER,
            DEFAULT_FEEDIN_ADJUSTMENT_MULTIPLIER,
            "feedin_adjustment_multiplier",
        )
        feed_offset = extractor.get_float(
            CONF_FEEDIN_ADJUSTMENT_OFFSET,
            DEFAULT_FEEDIN_ADJUSTMENT_OFFSET,
            "feedin_adjustment_offset",
        )
        buy_vat_multiplier = extractor.get_float(
            CONF_BUY_VAT_MULTIPLIER,
            DEFAULT_BUY_VAT_MULTIPLIER,
            "buy_vat_multiplier",
        )

        # Extract price thresholds
        price_threshold = extractor.get_float(
            CONF_PRICE_THRESHOLD, DEFAULT_PRICE_THRESHOLD, "price_threshold"
        )
        very_low_threshold_pct = extractor.get_float(
            CONF_VERY_LOW_PRICE_THRESHOLD,
            DEFAULT_VERY_LOW_PRICE_THRESHOLD,
            "very_low_price_threshold",
        )
        very_low_threshold_ratio = max(0.0, min(very_low_threshold_pct / 100.0, 1.0))
        feedin_threshold = extractor.get_float(
            CONF_FEEDIN_PRICE_THRESHOLD,
            DEFAULT_FEEDIN_PRICE_THRESHOLD,
            "feedin_price_threshold",
        )

        # Extract SOC thresholds
        min_soc_threshold = extractor.get_float(
            CONF_MIN_SOC_THRESHOLD, DEFAULT_MIN_SOC, "min_soc_threshold"
        )
        max_soc_threshold = extractor.get_float(
            CONF_MAX_SOC_THRESHOLD, DEFAULT_MAX_SOC, "max_soc_threshold"
        )
        arbitrage_mode_reserve_soc = extractor.get_float(
            CONF_ARBITRAGE_MODE_RESERVE_SOC,
            DEFAULT_ARBITRAGE_MODE_RESERVE_SOC,
            "arbitrage_mode_reserve_soc",
        )
        arbitrage_mode_reserve_soc_sunny = extractor.get_float(
            CONF_ARBITRAGE_MODE_RESERVE_SOC_SUNNY,
            DEFAULT_ARBITRAGE_MODE_RESERVE_SOC_SUNNY,
            "arbitrage_mode_reserve_soc_sunny",
        )
        arbitrage_mode_reserve_soc_sunny = max(
            0.0, min(100.0, arbitrage_mode_reserve_soc_sunny)
        )
        max_soc_threshold_sunny = extractor.get_float(
            CONF_MAX_SOC_THRESHOLD_SUNNY,
            DEFAULT_MAX_SOC_SUNNY,
            "max_soc_threshold_sunny",
        )
        max_soc_threshold_solar = extractor.get_float(
            CONF_MAX_SOC_THRESHOLD_SOLAR,
            DEFAULT_MAX_SOC_SOLAR,
            "max_soc_threshold_solar",
        )
        max_soc_threshold_solar = max(0.0, min(max_soc_threshold_solar, 100.0))
        sunny_forecast_threshold_kwh = extractor.get_float(
            CONF_SUNNY_FORECAST_THRESHOLD_KWH,
            DEFAULT_SUNNY_FORECAST_THRESHOLD_KWH,
            "sunny_forecast_threshold_kwh",
        )
        sunny_forecast_threshold_kwh = max(0.0, sunny_forecast_threshold_kwh)
        emergency_soc_threshold = extractor.get_float(
            CONF_EMERGENCY_SOC_THRESHOLD,
            DEFAULT_EMERGENCY_SOC,
            "emergency_soc_threshold",
        )
        predictive_min_soc = extractor.get_float(
            CONF_PREDICTIVE_CHARGING_MIN_SOC,
            DEFAULT_PREDICTIVE_CHARGING_MIN_SOC,
            "predictive_charging_min_soc",
        )

        # Extract boolean flags
        use_dynamic_threshold = extractor.get_bool(
            CONF_USE_DYNAMIC_THRESHOLD, DEFAULT_USE_DYNAMIC_THRESHOLD
        )
        use_average_threshold = extractor.get_bool(
            CONF_USE_AVERAGE_THRESHOLD, DEFAULT_USE_AVERAGE_THRESHOLD
        )
        dynamic_threshold_confidence = extractor.get_float(
            CONF_DYNAMIC_THRESHOLD_CONFIDENCE,
            DEFAULT_DYNAMIC_THRESHOLD_CONFIDENCE,
            "dynamic_threshold_confidence",
        )

        # Extract SOC-based price multiplier settings
        soc_price_multiplier_max = extractor.get_float(
            CONF_SOC_PRICE_MULTIPLIER_MAX,
            DEFAULT_SOC_PRICE_MULTIPLIER_MAX,
            "soc_price_multiplier_max",
        )
        # Clamp multiplier to sensible range (1.0 to 2.0)
        soc_price_multiplier_max = max(1.0, min(soc_price_multiplier_max, 2.0))

        soc_buffer_target = extractor.get_float(
            CONF_SOC_BUFFER_TARGET, DEFAULT_SOC_BUFFER_TARGET, "soc_buffer_target"
        )
        # Clamp buffer target to sensible range (must be > emergency threshold)
        soc_buffer_target = max(
            emergency_soc_threshold + 10, min(soc_buffer_target, 80)
        )

        return cls(
            max_grid_power=max_grid_power,
            base_grid_setpoint=base_grid_setpoint,
            max_inverter_power=max_inverter_power,
            inverter_export_limit=inverter_export_limit,
            inverter_export_deadband=inverter_export_deadband,
            inverter_derating_unused_release_minutes=inverter_derating_unused_release_minutes,
            inverter_derating_soc_bypass_threshold=inverter_derating_soc_bypass_threshold,
            max_battery_power=max_battery_power,
            max_car_power=max_car_power,
            min_car_charging_threshold=min_car_threshold,
            min_car_charging_duration=min_car_duration,
            car_permissive_threshold_multiplier=car_permissive_multiplier,
            car_use_battery_arbitrage=car_use_battery_arbitrage,
            price_adjustment_multiplier=price_multiplier,
            price_adjustment_offset=price_offset,
            feedin_adjustment_multiplier=feed_multiplier,
            feedin_adjustment_offset=feed_offset,
            buy_vat_multiplier=buy_vat_multiplier,
            price_threshold=price_threshold,
            very_low_price_threshold_ratio=very_low_threshold_ratio,
            very_low_price_threshold_pct=very_low_threshold_pct,
            feedin_threshold=feedin_threshold,
            significant_solar_threshold=significant_solar_threshold,
            min_soc_threshold=min_soc_threshold,
            max_soc_threshold=max_soc_threshold,
            arbitrage_mode_reserve_soc=arbitrage_mode_reserve_soc,
            arbitrage_mode_reserve_soc_sunny=arbitrage_mode_reserve_soc_sunny,
            max_soc_threshold_sunny=max_soc_threshold_sunny,
            max_soc_threshold_solar=max_soc_threshold_solar,
            sunny_forecast_threshold_kwh=sunny_forecast_threshold_kwh,
            emergency_soc_threshold=emergency_soc_threshold,
            predictive_min_soc=predictive_min_soc,
            use_dynamic_threshold=use_dynamic_threshold,
            dynamic_threshold_confidence=dynamic_threshold_confidence,
            use_average_threshold=use_average_threshold,
            soc_price_multiplier_max=soc_price_multiplier_max,
            soc_buffer_target=soc_buffer_target,
            battery_capacities=sanitized_capacities,
        )


@dataclass(frozen=True, slots=True)
class CycleContext:
    """Read-only snapshot of shared decision inputs for one cycle snapshot.

    Populate it from the raw coordinator/decision data once for the snapshot
    that a group of decisions should read.
    Keep every shared signal here so downstream methods stop re-reading the
    mutable `data` dict.
    Compute derived flags and thresholds in `from_data`.
    Rebuild a fresh instance if upstream outputs or manual overrides mutate
    the underlying data for a later decision step.
    Never mutate a CycleContext instance.
    """

    current_price: float | None
    raw_current_price: float | None
    highest_price: float | None
    lowest_price: float | None
    next_price: float | None
    average_threshold: float | None
    transport_cost: float
    configured_price_threshold: float
    resolved_price_threshold: float
    effective_battery_price_threshold: float
    soc_price_multiplier: float
    threshold_relaxed: bool
    locked_car_threshold: float | None
    car_threshold_floor: float
    effective_car_price_threshold: float
    car_permissive_multiplier: float
    effective_car_permissive_multiplier: float
    has_price_data: bool
    battery_analysis: dict[str, Any]
    battery_average_soc: float | None
    battery_soc: tuple[dict[str, Any], ...]
    battery_stable_threshold: float | None
    solar_production: float
    solar_surplus: float
    solar_forecast_production: float | None
    house_consumption: float
    current_grid_power: float | None
    previous_grid_power: float | None
    monthly_grid_peak: float | None
    car_charging_power: float
    car_solar_only: bool
    car_grid_import_allowed: bool
    car_permissive_mode_active: bool
    car_peak_limited: bool
    previous_battery_grid_charging: bool
    battery_grid_charging_state_age_seconds: float | None
    battery_grid_charging_locked_threshold: float | None
    previous_car_charging: bool
    has_min_charging_window: bool
    battery_grid_charging: bool
    car_grid_charging: bool
    charger_limit: int
    arbitrage_mode_enabled: bool
    arbitrage_mode_active: bool
    arbitrage_mode_export_power: int
    arbitrage_mode_reserve_soc: float
    arbitrage_mode_export_active: bool
    arbitrage_pending_power: int
    negative_buy_mode_enabled: bool
    negative_buy_mode_active: bool
    negative_buy_curtail_solar: bool
    negative_buy_import_power: int
    allocated_car_solar: float
    remaining_solar: float
    car_arbitrage_power: int
    phase_mode: str | None
    phase_batteries_map: dict[str, list[dict[str, Any]]]
    phase_capacity_map: dict[str, float]
    phase_details_map: dict[str, dict[str, Any]]
    previous_inverter_derating_target: float | None
    previous_inverter_derating_unreached_since: datetime | None
    inverter_derating_evaluated_at: datetime | None
    price_analysis_overrides: dict[str, Any]
    arbitrage_mode_reason: str | None

    @classmethod
    def from_data(
        cls,
        data: dict[str, Any],
        settings: EngineSettings,
        battery_analysis: dict[str, Any],
        price_analysis: dict[str, Any],
        power_analysis: dict[str, Any],
        power_allocation: dict[str, Any],
    ) -> "CycleContext":
        """Create an immutable snapshot from the current decision data."""
        price_analysis = price_analysis or {}
        battery_analysis = battery_analysis or {}
        power_analysis = power_analysis or {}
        power_allocation = power_allocation or {}

        resolved_price_threshold = _safe_optional_float(
            price_analysis.get("price_threshold")
        )
        if resolved_price_threshold is None:
            resolved_price_threshold = float(settings.price_threshold)

        battery_stable_threshold = _safe_optional_float(
            data.get("battery_stable_threshold")
        )
        battery_threshold_floor = (
            battery_stable_threshold
            if battery_stable_threshold is not None
            else resolved_price_threshold
        )

        battery_average_soc = _safe_optional_float(battery_analysis.get("average_soc"))
        if battery_average_soc is not None:
            soc_price_multiplier = calculate_soc_price_multiplier(
                current_soc=battery_average_soc,
                emergency_soc=settings.emergency_soc_threshold,
                buffer_target_soc=settings.soc_buffer_target,
                max_multiplier=settings.soc_price_multiplier_max,
            )
        else:
            soc_price_multiplier = 1.0
        effective_battery_price_threshold = (
            battery_threshold_floor * soc_price_multiplier
        )

        previous_car_charging = bool(data.get(_PREVIOUS_CAR_CHARGING_KEY))
        battery_grid_charging_state_age_seconds = _safe_optional_float(
            data.get(_BATTERY_GRID_CHARGING_STATE_AGE_SECONDS_KEY)
        )
        battery_grid_charging_locked_threshold = _safe_optional_float(
            data.get(_BATTERY_GRID_CHARGING_LOCKED_THRESHOLD_KEY)
        )
        locked_car_threshold = _safe_optional_float(
            data.get(_CAR_CHARGING_LOCKED_THRESHOLD_KEY)
        )
        car_threshold_floor = (
            max(locked_car_threshold, resolved_price_threshold)
            if previous_car_charging and locked_car_threshold is not None
            else resolved_price_threshold
        )

        car_permissive_multiplier = settings.car_permissive_threshold_multiplier
        effective_car_permissive_multiplier = car_permissive_multiplier
        car_permissive_mode_active = bool(data.get("car_permissive_mode_active", False))
        effective_car_price_threshold = car_threshold_floor
        if car_permissive_mode_active and car_permissive_multiplier > 1.0:
            effective_car_permissive_multiplier = max(
                PERMISSIVE_MULTIPLIER_MIN,
                min(car_permissive_multiplier, PERMISSIVE_MULTIPLIER_MAX),
            )
            effective_car_price_threshold = (
                car_threshold_floor * effective_car_permissive_multiplier
            )

        battery_grid_charging = bool(data.get("battery_grid_charging", False))
        car_grid_charging = bool(data.get("car_grid_charging", False))
        arbitrage_mode_export_power = max(
            0,
            int(_safe_optional_float(data.get("arbitrage_mode_export_power")) or 0),
        )
        arbitrage_mode_reserve_soc = _safe_optional_float(
            data.get("arbitrage_mode_reserve_soc")
        )
        if arbitrage_mode_reserve_soc is None:
            arbitrage_mode_reserve_soc = settings.arbitrage_mode_reserve_soc
        arbitrage_mode_reserve_soc = max(
            0.0, min(100.0, float(arbitrage_mode_reserve_soc))
        )

        arbitrage_mode_active = bool(data.get("arbitrage_mode_active"))
        arbitrage_mode_export_active = (
            arbitrage_mode_active and arbitrage_mode_export_power > 0
        )

        allocated_car_solar = (
            _safe_optional_float(power_allocation.get("solar_for_car")) or 0.0
        ) + (
            _safe_optional_float(power_allocation.get("car_current_solar_usage")) or 0.0
        )

        arbitrage_plan = data.get("arbitrage_mode_plan") or {}
        arbitrage_pending_power = 0
        if arbitrage_mode_active and arbitrage_mode_export_power > 0:
            arbitrage_pending_power = arbitrage_mode_export_power
        elif (
            not arbitrage_mode_active
            and int(arbitrage_plan.get("selected_slots_count") or 0) > 0
            and float(arbitrage_plan.get("available_energy_kwh") or 0.0) > 0.0
        ):
            arbitrage_pending_power = max(
                0,
                int(
                    _safe_optional_float(arbitrage_plan.get("configured_export_cap_w"))
                    or 0
                ),
            )

        car_arbitrage_power = 0
        if (
            settings.car_use_battery_arbitrage
            and arbitrage_pending_power > 0
            and not battery_grid_charging
            and battery_average_soc is not None
            and battery_average_soc >= arbitrage_mode_reserve_soc
        ):
            car_arbitrage_power = arbitrage_pending_power

        return cls(
            current_price=_safe_optional_float(price_analysis.get("current_price")),
            raw_current_price=_safe_optional_float(
                price_analysis.get("raw_current_price")
            ),
            highest_price=_safe_optional_float(price_analysis.get("highest_price")),
            lowest_price=_safe_optional_float(price_analysis.get("lowest_price")),
            next_price=_safe_optional_float(price_analysis.get("next_price")),
            average_threshold=_safe_optional_float(data.get("average_threshold")),
            transport_cost=float(
                _safe_optional_float(
                    price_analysis.get("transport_cost", data.get("transport_cost"))
                )
                or 0.0
            ),
            configured_price_threshold=float(settings.price_threshold),
            resolved_price_threshold=float(resolved_price_threshold),
            effective_battery_price_threshold=float(effective_battery_price_threshold),
            soc_price_multiplier=float(soc_price_multiplier),
            threshold_relaxed=soc_price_multiplier > 1.0,
            locked_car_threshold=locked_car_threshold,
            car_threshold_floor=float(car_threshold_floor),
            effective_car_price_threshold=float(effective_car_price_threshold),
            car_permissive_multiplier=float(car_permissive_multiplier),
            effective_car_permissive_multiplier=float(
                effective_car_permissive_multiplier
            ),
            has_price_data=bool(price_analysis.get("data_available", False)),
            battery_analysis=dict(battery_analysis),
            battery_average_soc=battery_average_soc,
            battery_soc=tuple(data.get("battery_soc", []) or []),
            battery_stable_threshold=battery_stable_threshold,
            solar_production=float(
                _safe_optional_float(
                    power_analysis.get("solar_production", data.get("solar_production"))
                )
                or 0.0
            ),
            solar_surplus=float(
                _safe_optional_float(
                    power_analysis.get("solar_surplus", data.get("solar_surplus"))
                )
                or 0.0
            ),
            solar_forecast_production=_safe_optional_float(
                data.get("solar_forecast_production")
            ),
            house_consumption=float(
                _safe_optional_float(
                    power_analysis.get(
                        "house_consumption", data.get("house_consumption")
                    )
                )
                or 0.0
            ),
            current_grid_power=_safe_optional_float(data.get("grid_power")),
            previous_grid_power=_safe_optional_float(data.get("previous_grid_power")),
            monthly_grid_peak=_safe_optional_float(data.get("monthly_grid_peak")),
            car_charging_power=float(
                _safe_optional_float(
                    power_analysis.get(
                        "car_charging_power", data.get("car_charging_power")
                    )
                )
                or 0.0
            ),
            car_solar_only=bool(data.get("car_solar_only", False)),
            car_grid_import_allowed=bool(
                data.get("car_grid_import_allowed", car_grid_charging)
            ),
            car_permissive_mode_active=car_permissive_mode_active,
            car_peak_limited=bool(data.get("car_peak_limited")),
            previous_battery_grid_charging=bool(
                data.get(_PREVIOUS_BATTERY_GRID_CHARGING_KEY, False)
            ),
            battery_grid_charging_state_age_seconds=battery_grid_charging_state_age_seconds,
            battery_grid_charging_locked_threshold=battery_grid_charging_locked_threshold,
            previous_car_charging=previous_car_charging,
            has_min_charging_window=bool(data.get(_HAS_MIN_CHARGING_WINDOW_KEY)),
            battery_grid_charging=battery_grid_charging,
            car_grid_charging=car_grid_charging,
            charger_limit=max(
                0,
                int(_safe_optional_float(data.get("charger_limit")) or 0),
            ),
            arbitrage_mode_enabled=bool(data.get("arbitrage_mode_enabled")),
            arbitrage_mode_active=arbitrage_mode_active,
            arbitrage_mode_export_power=arbitrage_mode_export_power,
            arbitrage_mode_reserve_soc=arbitrage_mode_reserve_soc,
            arbitrage_mode_export_active=arbitrage_mode_export_active,
            arbitrage_pending_power=arbitrage_pending_power,
            negative_buy_mode_enabled=bool(data.get("negative_buy_mode_enabled")),
            negative_buy_mode_active=bool(data.get("negative_buy_mode_active")),
            negative_buy_curtail_solar=bool(data.get("negative_buy_curtail_solar")),
            negative_buy_import_power=max(
                0,
                int(_safe_optional_float(data.get("negative_buy_import_power")) or 0),
            ),
            allocated_car_solar=float(allocated_car_solar),
            remaining_solar=float(
                _safe_optional_float(power_allocation.get("remaining_solar")) or 0.0
            ),
            car_arbitrage_power=car_arbitrage_power,
            phase_mode=data.get(CONF_PHASE_MODE),
            phase_batteries_map=dict(data.get("phase_batteries") or {}),
            phase_capacity_map=dict(data.get("phase_capacity_map") or {}),
            phase_details_map=dict(data.get("phase_details") or {}),
            previous_inverter_derating_target=_safe_optional_float(
                data.get("previous_inverter_derating_target")
            ),
            previous_inverter_derating_unreached_since=_safe_optional_datetime(
                data.get("previous_inverter_derating_unreached_since")
            ),
            inverter_derating_evaluated_at=_safe_optional_datetime(
                data.get("inverter_derating_evaluated_at")
            ),
            price_analysis_overrides=dict(data.get("price_analysis_overrides") or {}),
            arbitrage_mode_reason=data.get("arbitrage_mode_reason"),
        )


@dataclass(frozen=True)
class CarDecisionContext:
    """Immutable snapshot of the variables that drive car charging decisions."""

    current_price: float | None
    base_threshold: float
    effective_threshold: float
    previous_charging: bool
    has_min_window: bool
    min_duration: int
    allocated_solar: float
    very_low_price: bool
    very_low_percent: float
    is_low_price_flag: bool
    effective_low_price: bool
    permissive_mode_active: bool
    permissive_multiplier: float

    @property
    def display_price(self) -> float:
        """Safe value for string formatting when price is missing."""
        return self.current_price if self.current_price is not None else 0.0

    @property
    def has_allocated_solar(self) -> bool:
        """Whether any solar power is earmarked for the car."""
        return self.allocated_solar > 0

    def format_price_comparison(self, operator: str = "≤") -> str:
        """Format price vs threshold comparison for logging."""
        return f"{self.display_price:.3f}€/kWh {operator} {self.effective_threshold:.3f}€/kWh"

    def format_solar_watts(self) -> str:
        """Format allocated solar power as string."""
        return f"{int(self.allocated_solar)}W"


class ChargingDecisionEngine:
    """Engine for making charging decisions based on multiple factors."""

    def __init__(self, hass: HomeAssistant, config: dict[str, Any]) -> None:
        """Initialize the decision engine."""
        self.hass = hass
        self.config = config
        self.validator = DataValidator()
        self.price_calculator = PriceCalculator()
        self._settings = EngineSettings.from_config(config, self.validator)
        self._inverter_derating = InverterDeratingCalculator(self._settings)
        self._feedin_decision = FeedInDecisionCalculator(self._settings)
        self._battery_analysis = BatteryAnalysisCalculator(
            self._settings, self.validator
        )
        self._price_analysis = PriceAnalysisCalculator(
            self._settings, self.price_calculator
        )
        self._car_charging = CarChargingDecisionCalculator(self._settings)
        self._solar_allocation = SolarAllocationCalculator(
            self._settings, self.validator
        )

        # Initialize strategy manager with dynamic threshold configuration
        self.strategy_manager = StrategyManager(
            use_dynamic_threshold=self._settings.use_dynamic_threshold
        )

        self._battery_charging = BatteryChargingDecisionCalculator(
            self._settings, self.strategy_manager, self.config
        )
        self._grid_setpoint = GridSetpointCalculator(
            self._settings, self._get_car_arbitrage_power
        )
        self._charger_limit = ChargerLimitCalculator(
            self._settings, self._grid_setpoint
        )
        self._override_recalculator = OverrideRecalculator(self)
        self._phase_distributor = PhaseDistributor(self._normalize_grid_components)
        self.power_validator = PowerAllocationValidator()

    def refresh_settings(self, config: dict[str, Any]) -> None:
        """Refresh engine settings from updated config.

        Call this after config changes to apply new thresholds immediately
        without recreating the entire decision engine.

        Args:
            config: The updated configuration dictionary.
        """
        self.config = config
        self._settings = EngineSettings.from_config(config, self.validator)
        self._inverter_derating = InverterDeratingCalculator(self._settings)
        self._feedin_decision = FeedInDecisionCalculator(self._settings)
        self._battery_analysis = BatteryAnalysisCalculator(
            self._settings, self.validator
        )
        self._price_analysis = PriceAnalysisCalculator(
            self._settings, self.price_calculator
        )
        self._car_charging = CarChargingDecisionCalculator(self._settings)
        self._solar_allocation.refresh(self._settings)
        self._battery_charging.refresh(self._settings, self.config)
        self._grid_setpoint.refresh(self._settings)
        self._charger_limit.refresh(self._settings)
        _LOGGER.debug("Decision engine settings refreshed")

    async def evaluate_charging_decision(self, data: dict[str, Any]) -> dict[str, Any]:
        """Evaluate whether to charge batteries and car from grid."""
        phase_mode = data.get(CONF_PHASE_MODE)
        if phase_mode == PHASE_MODE_THREE:
            return await self._evaluate_three_phase(data)
        return await self._evaluate_single_phase(data)

    def _get_safe_grid_setpoint(self, monthly_peak: float | None) -> int:
        """Delegate to the grid setpoint calculator."""
        return self._grid_setpoint.get_safe_setpoint(monthly_peak)

    async def _evaluate_single_phase(self, data: dict[str, Any]) -> dict[str, Any]:
        """Evaluate charging decisions for a single logical phase."""
        decision_data = self._initialize_decision_data()

        # Validate critical data availability
        current_price = data.get("current_price")
        if current_price is None:
            return self._create_no_data_decision(decision_data)

        # Analyze all aspects
        price_analysis = self._analyze_comprehensive_pricing(data)
        decision_data["price_analysis"] = price_analysis

        battery_analysis = self._analyze_battery_status(data.get("battery_soc", []))
        decision_data["battery_analysis"] = battery_analysis

        power_analysis = self._analyze_power_flow(data)
        decision_data["power_analysis"] = power_analysis

        solar_analysis = self._analyze_solar_production(data)
        decision_data["solar_analysis"] = solar_analysis

        time_context = TimeContext.get_current_context()
        decision_data["time_context"] = time_context

        # Allocate solar power
        power_allocation = self._allocate_solar_power(power_analysis, battery_analysis)
        decision_data["power_allocation"] = power_allocation

        # Validate power allocation
        is_valid, error = self.power_validator.validate_allocation(
            power_allocation,
            power_analysis.get("solar_surplus", 0),
            self._settings.max_battery_power,
            self._settings.max_car_power,
        )
        if not is_valid:
            _LOGGER.warning("Power allocation validation failed: %s", error)

        # Resolve effective max SOC for grid charging (sunny day logic)
        grid_battery_analysis = self._apply_sunny_day_grid_limit(battery_analysis, data)
        decision_data["sunny_day_active"] = grid_battery_analysis.get(
            "max_soc_threshold"
        ) != battery_analysis.get("max_soc_threshold")

        cycle_ctx = CycleContext.from_data(
            {**data, **decision_data},
            self._settings,
            grid_battery_analysis,
            price_analysis,
            power_analysis,
            power_allocation,
        )
        cycle_ctx_log = asdict(cycle_ctx)
        for field_name in (
            "battery_analysis",
            "phase_batteries_map",
            "phase_capacity_map",
            "phase_details_map",
            "price_analysis_overrides",
        ):
            cycle_ctx_log.pop(field_name, None)
        _LOGGER.debug("Cycle context: %s", cycle_ctx_log)

        # Make charging decisions (use grid-specific battery analysis)
        battery_decision = self._decide_battery_grid_charging(
            price_analysis,
            grid_battery_analysis,
            power_allocation,
            power_analysis,
            time_context,
            data,
            ctx=cycle_ctx,
        )
        # Surface the effective battery price threshold so the coordinator can
        # capture it at OFF->ON transitions (used to lock the anti-flap hold).
        battery_decision.setdefault(
            "battery_effective_threshold",
            cycle_ctx.effective_battery_price_threshold,
        )
        decision_data.update(battery_decision)

        cycle_ctx = CycleContext.from_data(
            {**data, **decision_data},
            self._settings,
            grid_battery_analysis,
            price_analysis,
            power_analysis,
            power_allocation,
        )

        # Get dynamic threshold from strategy manager (if dynamic pricing enabled)
        context = {
            "price_analysis": price_analysis,
            "battery_analysis": grid_battery_analysis,
            "power_allocation": power_allocation,
            "power_analysis": power_analysis,
            "time_context": time_context,
            "config": self.config,
            "settings": self._settings,
            "battery_stable_threshold": cycle_ctx.battery_stable_threshold,
            "effective_threshold": cycle_ctx.effective_battery_price_threshold,
            "soc_price_multiplier": cycle_ctx.soc_price_multiplier,
            "threshold_relaxed": cycle_ctx.threshold_relaxed,
        }
        dynamic_threshold = self.strategy_manager.get_dynamic_threshold(context)
        if dynamic_threshold is not None:
            price_analysis["dynamic_threshold"] = dynamic_threshold

        car_decision_input = {**data, **decision_data}
        car_decision = self._decide_car_grid_charging(
            price_analysis,
            grid_battery_analysis,
            power_allocation,
            car_decision_input,
            ctx=cycle_ctx,
        )
        data[_CAR_CHARGING_LOCKED_THRESHOLD_KEY] = car_decision_input.get(
            _CAR_CHARGING_LOCKED_THRESHOLD_KEY
        )
        decision_data.update(car_decision)

        cycle_ctx = CycleContext.from_data(
            {**data, **decision_data},
            self._settings,
            grid_battery_analysis,
            price_analysis,
            power_analysis,
            power_allocation,
        )

        charger_limit_decision = self._calculate_charger_limit(
            price_analysis,
            grid_battery_analysis,
            power_allocation,
            {**data, **decision_data},
            ctx=cycle_ctx,
        )
        decision_data.update(charger_limit_decision)

        # Rebuild the context after charger_limit so downstream consumers
        # (grid_setpoint, feedin) see an up-to-date ctx.charger_limit. Today
        # neither reads that field — grid_setpoint receives charger_limit as
        # an explicit argument — but rebuilding here removes an implicit
        # "don't read ctx.charger_limit" contract that future edits could
        # easily violate.
        cycle_ctx = CycleContext.from_data(
            {**data, **decision_data},
            self._settings,
            grid_battery_analysis,
            price_analysis,
            power_analysis,
            power_allocation,
        )

        grid_setpoint_decision = self._calculate_grid_setpoint(
            price_analysis,
            grid_battery_analysis,
            power_allocation,
            {**data, **decision_data},
            decision_data.get("charger_limit", 0),
            ctx=cycle_ctx,
        )
        decision_data.update(grid_setpoint_decision)

        feedin_decision = self._decide_feedin_solar(
            price_analysis, power_allocation, cycle_ctx
        )
        decision_data.update(feedin_decision)

        decision_data_for_downstream = {**data, **decision_data}

        inverter_derating_decision = self._calculate_inverter_derating_target(
            decision_data_for_downstream
        )
        decision_data.update(inverter_derating_decision)

        return decision_data

    async def _evaluate_three_phase(self, data: dict[str, Any]) -> dict[str, Any]:
        """Evaluate charging decisions when operating in three-phase mode."""
        # Run the standard single-phase evaluation on aggregated totals
        aggregated_data = dict(data)
        aggregated_data["phase_mode"] = PHASE_MODE_SINGLE
        overall_decision = await self._evaluate_single_phase(aggregated_data)

        phase_results = self._distribute_phase_decisions(overall_decision, data)
        overall_decision["phase_results"] = phase_results
        overall_decision["phase_mode"] = PHASE_MODE_THREE
        return overall_decision

    def _distribute_phase_decisions(
        self,
        overall_decision: dict[str, Any],
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Delegate to the phase distributor.

        This method takes the overall charging decision (which was computed on
        aggregated system totals) and distributes power allocations across the
        three phases based on battery assignments and capacity weighting.

        Algorithm:
        1. Battery power is distributed proportionally by capacity
           - Each phase with assigned batteries receives power based on its
             share of total battery capacity
           - If phase 1 has 10kWh and phase 2 has 5kWh, phase 1 gets 66.7%
             and phase 2 gets 33.3% of battery charging power

        2. Car power is distributed equally across phases with car sensors
           - Each phase gets 1/N of the total car power allocation
           - If car power is 11kW and 2 phases have car sensors, each gets 5.5kW

        3. Grid setpoint = battery allocation + car allocation per phase
           - Each phase gets a grid_setpoint value for grid import control
           - Phases without assignments get 0W setpoint

        Returns:
            Dict mapping phase_id -> {
                "battery_allowed": bool,
                "car_allowed": bool,
                "grid_setpoint": int (W),
                "grid_components": {"battery": int, "car": int},
                "charger_limit": int (W),
                "battery_entities": list[str],
                "capacity_share": float (0-1)
            }
        """
        return self._phase_distributor.distribute_phase_decisions(
            overall_decision, data
        )

    def _distribute_quantity(
        self,
        total: int,
        phases: list[str],
        weights: dict[str, float],
    ) -> dict[str, int]:
        """Delegate to the phase distributor."""
        return PhaseDistributor.distribute_quantity(total, phases, weights)

    def _initialize_decision_data(self) -> dict[str, Any]:
        """Initialize the decision data structure."""
        return {
            "battery_grid_charging": False,
            "car_grid_charging": False,
            "car_grid_import_allowed": False,
            # car_solar_only MUST be seeded here so that a stale True from a
            # prior solar-only cycle stored in coordinator state (passed back as
            # `data`) is overwritten by the current cycle's decision even when
            # the car-decision path (e.g. arbitrage) does not set the key
            # explicitly.  Without this seed the stale flag leaks into
            # decision_data_for_downstream and causes _calculate_charger_limit
            # and _calculate_grid_setpoint to use the solar-only branch instead
            # of the arbitrage branch.
            "car_solar_only": False,
            "sunny_day_active": False,
            "battery_grid_charging_reason": "No decision made",
            "car_grid_charging_reason": "No decision made",
            "charger_limit": 0,
            "grid_setpoint": 0,
            "grid_components": {"battery": 0, "car": 0},
            "inverter_derating_target": None,
            "charger_limit_reason": "No decision made",
            "grid_setpoint_reason": "No decision made",
            "inverter_derating_reason": "No decision made",
            "inverter_derating_alarm": False,
            "inverter_derating_alarm_reason": "No alarm",
            "feedin_solar": False,
            "feedin_solar_reason": "No decision made",
            "next_evaluation": dt_util.utcnow()
            + timedelta(minutes=DEFAULT_SYSTEM_LIMITS.evaluation_interval),
            "price_analysis": {},
            "power_analysis": {},
            "battery_analysis": {},
            "solar_analysis": {},
            "phase_results": {},
            "phase_mode": PHASE_MODE_SINGLE,
        }

    def _create_no_data_decision(self, decision_data: dict[str, Any]) -> dict[str, Any]:
        """Create decision when no price data is available."""
        reason = (
            "No current price data available - all grid charging disabled for safety"
        )
        decision_data["battery_grid_charging_reason"] = reason
        decision_data["car_grid_charging_reason"] = reason
        decision_data["charger_limit_reason"] = "No price data - limiting to solar only"
        decision_data["grid_setpoint_reason"] = "No price data - grid setpoint set to 0"
        decision_data["feedin_solar_reason"] = (
            "No price data - feed-in decision disabled"
        )
        decision_data["inverter_derating_reason"] = (
            "No price data - inverter derating target unavailable"
        )
        decision_data["inverter_derating_alarm_reason"] = (
            "No price data - derating alarm unavailable"
        )
        _LOGGER.warning("Critical price data unavailable - disabling all grid charging")
        return decision_data

    def _analyze_comprehensive_pricing(self, data: dict[str, Any]) -> dict[str, Any]:
        """Delegate to the price analysis calculator collaborator."""
        return self._price_analysis.analyze(data)

    def _analyze_power_flow(self, data: dict[str, Any]) -> dict[str, Any]:
        """Analyze current power flow and consumption."""
        solar_production = data.get("solar_production", 0) or 0
        house_consumption = data.get("house_consumption", 0) or 0
        solar_surplus = data.get("solar_surplus", 0) or 0
        car_charging_power = data.get("car_charging_power", 0) or 0

        # Validate power values
        solar_production = self.validator.validate_power_value(
            solar_production, name="solar_production", max_value=MAX_POWER_VALIDATION_W
        )
        house_consumption = self.validator.validate_power_value(
            house_consumption,
            name="house_consumption",
            max_value=MAX_POWER_VALIDATION_W,
        )
        car_charging_power = self.validator.validate_power_value(
            car_charging_power,
            name="car_charging",
            max_value=MAX_CAR_POWER_VALIDATION_W,
        )

        significant_solar_threshold = self._settings.significant_solar_threshold

        house_consumption_without_car = max(0, house_consumption - car_charging_power)
        solar_coverage_ratio = (
            (solar_production / house_consumption) if house_consumption > 0 else 0
        )

        return {
            "solar_production": solar_production,
            "house_consumption": house_consumption,
            "house_consumption_without_car": house_consumption_without_car,
            "solar_surplus": solar_surplus,
            "car_charging_power": car_charging_power,
            "has_solar_production": solar_production > 0,
            "has_solar_surplus": solar_surplus > 0,
            "significant_solar_surplus": solar_surplus > significant_solar_threshold,
            "car_currently_charging": car_charging_power > 0,
            "available_surplus_for_batteries": max(
                0, solar_surplus - car_charging_power
            ),
            "significant_solar_threshold": significant_solar_threshold,
            "solar_coverage_ratio": solar_coverage_ratio,
            "has_excess_solar_available": solar_surplus > 0,
        }

    def _analyze_solar_production(self, data: dict[str, Any]) -> dict[str, Any]:
        """Analyze solar production status."""
        solar_production = data.get("solar_production", 0) or 0
        house_consumption = data.get("house_consumption", 0) or 0
        solar_surplus = data.get("solar_surplus", 0) or 0

        is_producing = solar_production > 0
        has_available_surplus = solar_surplus > 0
        production_efficiency = (
            min(1.0, solar_production / DEFAULT_POWER_ESTIMATES.max_solar_production)
            if solar_production > 0
            else 0
        )

        return {
            "current_production": solar_production,
            "house_consumption": house_consumption,
            "available_surplus": solar_surplus,
            "is_producing": is_producing,
            "has_available_surplus": has_available_surplus,
            "production_efficiency": production_efficiency,
        }

    def _allocate_solar_power(
        self,
        power_analysis: dict[str, Any],
        battery_analysis: dict[str, Any],
    ) -> dict[str, Any]:
        """Delegate to the solar allocation calculator."""
        return self._solar_allocation.allocate(power_analysis, battery_analysis)

    def _analyze_battery_status(
        self, battery_soc_data: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Delegate to the battery analysis calculator collaborator."""
        return self._battery_analysis.analyze(battery_soc_data)

    def _create_no_battery_result(self) -> dict[str, Any]:
        """Delegate to the battery analysis calculator collaborator."""
        return self._battery_analysis._create_no_battery_result()

    def _create_unavailable_battery_result(self, count: int) -> dict[str, Any]:
        """Delegate to the battery analysis calculator collaborator."""
        return self._battery_analysis._create_unavailable_battery_result(count)

    def _apply_sunny_day_grid_limit(
        self,
        battery_analysis: dict[str, Any],
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Apply sunny day max SOC limit for grid charging decisions.

        When the solar forecast for the upcoming period exceeds the configured
        sunny forecast threshold (kWh), reduce the grid charging max SOC to the
        sunny-day threshold so that grid charging stops earlier and leaves room
        for free solar charging.

        Returns a (possibly modified) copy of battery_analysis.
        """
        solar_forecast = _safe_optional_float(data.get("solar_forecast_production"))
        if solar_forecast is None:
            return battery_analysis

        sunny_threshold = self._settings.max_soc_threshold_sunny
        normal_threshold = self._settings.max_soc_threshold

        # If sunny threshold >= normal, feature effectively disabled
        if sunny_threshold >= normal_threshold:
            return battery_analysis

        sunny_production_threshold = self._settings.sunny_forecast_threshold_kwh

        if solar_forecast >= sunny_production_threshold:
            _LOGGER.debug(
                "Sunny day detected: forecast %.1f kWh >= %.1f kWh threshold - "
                "grid max SOC reduced from %.0f%% to %.0f%%",
                solar_forecast,
                sunny_production_threshold,
                normal_threshold,
                sunny_threshold,
            )
            # Create modified copy for grid charging decisions only
            grid_analysis = dict(battery_analysis)
            grid_analysis["max_soc_threshold"] = sunny_threshold
            grid_analysis["sunny_day_grid_soc_hysteresis_percent"] = (
                _SUNNY_DAY_GRID_SOC_HYSTERESIS_PERCENT
            )
            average_soc = grid_analysis.get("average_soc")
            if average_soc is not None:
                stop_threshold = sunny_threshold
                if data.get(_PREVIOUS_BATTERY_GRID_CHARGING_KEY):
                    stop_threshold = min(
                        sunny_threshold + _SUNNY_DAY_GRID_SOC_HYSTERESIS_PERCENT,
                        100.0,
                    )
                grid_analysis["grid_charge_stop_soc_threshold"] = stop_threshold
                grid_analysis["batteries_full"] = average_soc >= (stop_threshold - 1e-9)
                grid_analysis["remaining_capacity_percent"] = (
                    stop_threshold - average_soc
                )
            return grid_analysis

        _LOGGER.debug(
            "Not a sunny day: forecast %.1f kWh < %.1f kWh threshold",
            solar_forecast,
            sunny_production_threshold,
        )
        # Apply general SOC hysteresis to prevent boundary oscillation.
        # Once charging reaches the max threshold, raise the stop point by 2%
        # so small SOC fluctuations don't toggle charging on/off.
        grid_analysis = dict(battery_analysis)
        average_soc = grid_analysis.get("average_soc")
        if average_soc is not None:
            stop_threshold = normal_threshold
            if data.get(_PREVIOUS_BATTERY_GRID_CHARGING_KEY):
                stop_threshold = min(
                    normal_threshold + _SUNNY_DAY_GRID_SOC_HYSTERESIS_PERCENT,
                    100.0,
                )
            grid_analysis["grid_charge_stop_soc_threshold"] = stop_threshold
            grid_analysis["batteries_full"] = average_soc >= (stop_threshold - 1e-9)
            grid_analysis["remaining_capacity_percent"] = stop_threshold - average_soc
        return grid_analysis

    def _calculate_weighted_average_soc(self, batteries: list[dict[str, Any]]) -> float:
        """Delegate to the battery analysis calculator collaborator."""
        return self._battery_analysis.calculate_weighted_average_soc(batteries)

    def _decide_battery_grid_charging(
        self,
        price_analysis: dict[str, Any],
        battery_analysis: dict[str, Any],
        power_allocation: dict[str, Any],
        power_analysis: dict[str, Any],
        time_context: dict[str, Any],
        data: dict[str, Any],
        ctx: CycleContext | None = None,
    ) -> dict[str, Any]:
        """Delegate to the battery charging decision calculator."""
        if ctx is None:
            ctx = CycleContext.from_data(
                data,
                self._settings,
                battery_analysis,
                price_analysis,
                power_analysis,
                power_allocation,
            )
        return self._battery_charging.decide(
            price_analysis,
            battery_analysis,
            power_allocation,
            power_analysis,
            time_context,
            ctx,
        )

    def _decide_car_grid_charging(
        self,
        price_analysis: dict[str, Any],
        battery_analysis: dict[str, Any],
        power_allocation: dict[str, Any],
        data: dict[str, Any],
        ctx: CycleContext | None = None,
    ) -> dict[str, Any]:
        """Delegate to the car charging decision calculator."""
        if ctx is None:
            ctx = CycleContext.from_data(
                data,
                self._settings,
                battery_analysis,
                price_analysis,
                {},
                power_allocation,
            )
        return self._car_charging.decide(
            self, price_analysis, battery_analysis, power_allocation, data, ctx
        )

    def _decide_feedin_solar(
        self,
        price_analysis: dict[str, Any],
        power_allocation: dict[str, Any],
        ctx: CycleContext | None = None,
    ) -> dict[str, Any]:
        """Delegate to the feed-in decision calculator collaborator."""
        if ctx is None:
            ctx = CycleContext.from_data(
                {},
                self._settings,
                {},
                price_analysis,
                {},
                power_allocation,
            )
        return self._feedin_decision.decide(ctx)

    def _calculate_inverter_derating_target(
        self,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Delegate to the inverter-derating calculator collaborator."""
        return self._inverter_derating.calculate(data)

    def _build_car_decision_context(
        self,
        price_analysis: dict[str, Any],
        ctx: CycleContext,
    ) -> CarDecisionContext:
        """Delegate to the car charging decision calculator."""
        return self._car_charging.build_context(price_analysis, ctx)

    def _car_decision_for_very_low_price(
        self,
        context: CarDecisionContext,
        ctx: CycleContext,
        data: dict[str, Any],
    ) -> CarChargingDecision:
        """Delegate to the car charging decision calculator."""
        return self._car_charging.decision_for_very_low_price(context, ctx, data)

    def _car_decision_for_low_price(
        self,
        context: CarDecisionContext,
        ctx: CycleContext,
        data: dict[str, Any],
    ) -> CarChargingDecision:
        """Delegate to the car charging decision calculator."""
        return self._car_charging.decision_for_low_price(context, ctx, data)

    def _car_decision_for_high_price(
        self,
        context: CarDecisionContext,
        ctx: CycleContext,
        data: dict[str, Any],
    ) -> CarChargingDecision:
        """Delegate to the car charging decision calculator."""
        return self._car_charging.decision_for_high_price(context, ctx, data)

    def _get_car_arbitrage_power(self, ctx: CycleContext) -> int:
        """Delegate to the charger limit calculator."""
        return self._charger_limit.get_car_arbitrage_power(ctx)

    def _calculate_charger_limit(
        self,
        price_analysis: dict[str, Any],
        battery_analysis: dict[str, Any],
        power_allocation: dict[str, Any],
        data: dict[str, Any],
        ctx: CycleContext | None = None,
    ) -> dict[str, Any]:
        """Delegate to the charger limit calculator."""
        if ctx is None:
            ctx = CycleContext.from_data(
                data,
                self._settings,
                battery_analysis,
                price_analysis,
                {},
                power_allocation,
            )
        return self._charger_limit.calculate(battery_analysis, ctx)

    def _calculate_grid_setpoint(
        self,
        price_analysis: dict[str, Any],
        battery_analysis: dict[str, Any],
        power_allocation: dict[str, Any],
        data: dict[str, Any],
        charger_limit: int,
        ctx: CycleContext | None = None,
    ) -> dict[str, Any]:
        """Delegate to the grid setpoint calculator."""
        if ctx is None:
            ctx = CycleContext.from_data(
                data,
                self._settings,
                battery_analysis,
                price_analysis,
                {},
                power_allocation,
            )
        return self._grid_setpoint.calculate(
            price_analysis, battery_analysis, power_allocation, charger_limit, ctx
        )

    def _normalize_grid_components(
        self,
        decision: dict[str, Any],
        ctx: CycleContext | None = None,
    ) -> dict[str, int]:
        """Delegate to the override recalculator."""
        return self._override_recalculator.normalize_grid_components(decision, ctx)

    def recalculate_after_override(
        self,
        baseline_data: dict[str, Any],
        decision: dict[str, Any],
        override_targets: set[str],
    ) -> dict[str, Any]:
        """Delegate to the override recalculator."""
        return self._override_recalculator.recalculate_after_override(
            baseline_data, decision, override_targets
        )
