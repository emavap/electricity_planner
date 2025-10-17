"""Decision engine for electricity planning."""
from __future__ import annotations

import logging
from datetime import timedelta
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, TypedDict

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import (
    CONF_MIN_SOC_THRESHOLD,
    CONF_MAX_SOC_THRESHOLD,
    CONF_PRICE_THRESHOLD,
    CONF_BATTERY_CAPACITIES,
    CONF_EMERGENCY_SOC_THRESHOLD,
    CONF_VERY_LOW_PRICE_THRESHOLD,
    CONF_SIGNIFICANT_SOLAR_THRESHOLD,
    CONF_FEEDIN_PRICE_THRESHOLD,
    CONF_MAX_BATTERY_POWER,
    CONF_MAX_CAR_POWER,
    CONF_MAX_GRID_POWER,
    CONF_MIN_CAR_CHARGING_THRESHOLD,
    CONF_SOLAR_PEAK_EMERGENCY_SOC,
    CONF_PREDICTIVE_CHARGING_MIN_SOC,
    CONF_BASE_GRID_SETPOINT,
    CONF_USE_DYNAMIC_THRESHOLD,
    CONF_DYNAMIC_THRESHOLD_CONFIDENCE,
    CONF_USE_AVERAGE_THRESHOLD,
    CONF_MIN_CAR_CHARGING_DURATION,
    CONF_PRICE_ADJUSTMENT_MULTIPLIER,
    CONF_PRICE_ADJUSTMENT_OFFSET,
    CONF_FEEDIN_ADJUSTMENT_MULTIPLIER,
    CONF_FEEDIN_ADJUSTMENT_OFFSET,
    DEFAULT_MIN_SOC,
    DEFAULT_MAX_SOC,
    DEFAULT_PRICE_THRESHOLD,
    DEFAULT_EMERGENCY_SOC,
    DEFAULT_VERY_LOW_PRICE_THRESHOLD,
    DEFAULT_SIGNIFICANT_SOLAR_THRESHOLD,
    DEFAULT_FEEDIN_PRICE_THRESHOLD,
    DEFAULT_MAX_BATTERY_POWER,
    DEFAULT_MAX_CAR_POWER,
    DEFAULT_MAX_GRID_POWER,
    DEFAULT_MIN_CAR_CHARGING_THRESHOLD,
    DEFAULT_SOLAR_PEAK_EMERGENCY_SOC,
    DEFAULT_PREDICTIVE_CHARGING_MIN_SOC,
    DEFAULT_BASE_GRID_SETPOINT,
    DEFAULT_USE_DYNAMIC_THRESHOLD,
    DEFAULT_DYNAMIC_THRESHOLD_CONFIDENCE,
    DEFAULT_USE_AVERAGE_THRESHOLD,
    DEFAULT_MIN_CAR_CHARGING_DURATION,
    DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER,
    DEFAULT_PRICE_ADJUSTMENT_OFFSET,
    DEFAULT_FEEDIN_ADJUSTMENT_MULTIPLIER,
    DEFAULT_FEEDIN_ADJUSTMENT_OFFSET,
    DEFAULT_MONTHLY_PEAK_SAFETY_MARGIN,
)

from .defaults import (
    DEFAULT_TIME_SCHEDULE,
    DEFAULT_POWER_ESTIMATES,
    DEFAULT_ALGORITHM_THRESHOLDS,
    DEFAULT_SYSTEM_LIMITS,
)

from .helpers import (
    DataValidator,
    PriceCalculator,
    TimeContext,
    PowerAllocationValidator,
    format_reason,
    apply_price_adjustment,
)

from .strategies import StrategyManager

_LOGGER = logging.getLogger(__name__)

# Internal state keys
_CAR_CHARGING_LOCKED_THRESHOLD_KEY = "car_charging_locked_threshold"
_PREVIOUS_CAR_CHARGING_KEY = "previous_car_charging"
_HAS_MIN_CHARGING_WINDOW_KEY = "has_min_charging_window"


class PriceCategory(Enum):
    """Price category classification for car charging decisions."""
    VERY_LOW = "very_low"
    LOW = "low"
    HIGH = "high"


class CarChargingDecision(TypedDict, total=False):
    """Type definition for car charging decision results."""
    car_grid_charging: bool
    car_grid_charging_reason: str
    car_solar_only: bool


class BatteryChargingDecision(TypedDict, total=False):
    """Type definition for battery charging decision results."""
    battery_grid_charging: bool
    battery_grid_charging_reason: str
    strategy_trace: List[str]


class FeedinDecision(TypedDict, total=False):
    """Type definition for solar feed-in decision results."""
    feedin_solar: bool
    feedin_solar_reason: str
    feedin_effective_price: Optional[float]


def _safe_optional_float(value: Any) -> Optional[float]:
    """Best-effort conversion of arbitrary input to float."""
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


@dataclass
class ConfigExtractor:
    """Helper to extract and validate configuration values."""

    config: Dict[str, Any]
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
        name: Optional[str] = None,
    ) -> float:
        """Extract and coerce a float value."""
        value = self.config.get(key, default)
        try:
            return float(value)
        except (TypeError, ValueError):
            _LOGGER.warning(
                "%s value %s invalid, using default %s",
                name or key, value, default
            )
            return float(default)

    def get_int(
        self,
        key: str,
        default: int,
        name: Optional[str] = None,
        minimum: Optional[int] = None,
        maximum: Optional[int] = None,
    ) -> int:
        """Extract and coerce an int value with optional bounds."""
        coerced = self.get_float(key, default, name or key)
        if minimum is not None and coerced < minimum:
            _LOGGER.warning(
                "%s value %s below minimum %s, using %s",
                name or key, coerced, minimum, default
            )
            return int(default)
        if maximum is not None and coerced > maximum:
            _LOGGER.warning(
                "%s value %s above maximum %s, using %s",
                name or key, coerced, maximum, default
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
    max_battery_power: int
    max_car_power: int
    min_car_charging_threshold: int
    min_car_charging_duration: int
    price_adjustment_multiplier: float
    price_adjustment_offset: float
    feedin_adjustment_multiplier: float
    feedin_adjustment_offset: float
    price_threshold: float
    very_low_price_threshold_ratio: float
    very_low_price_threshold_pct: float
    feedin_threshold: float
    significant_solar_threshold: int
    min_soc_threshold: float
    max_soc_threshold: float
    emergency_soc_threshold: float
    predictive_min_soc: float
    solar_peak_emergency_soc: float
    use_dynamic_threshold: bool
    dynamic_threshold_confidence: float
    use_average_threshold: bool
    battery_capacities: Dict[str, float]

    @classmethod
    def from_config(cls, config: Dict[str, Any], validator: DataValidator) -> "EngineSettings":
        """Create sanitized settings from raw configuration."""
        extractor = ConfigExtractor(config, validator)

        # Extract battery capacities
        battery_capacity_config = config.get(CONF_BATTERY_CAPACITIES, {}) or {}
        sanitized_capacities: Dict[str, float] = {}
        for entity_id, raw_capacity in battery_capacity_config.items():
            try:
                capacity = float(raw_capacity)
            except (TypeError, ValueError):
                _LOGGER.warning("Battery capacity for %s invalid (%s) - skipping", entity_id, raw_capacity)
                continue
            if capacity <= 0:
                _LOGGER.warning("Battery capacity for %s non-positive (%s) - skipping", entity_id, capacity)
                continue
            sanitized_capacities[entity_id] = capacity

        # Extract power settings
        max_grid_power = extractor.get_power_setting(
            CONF_MAX_GRID_POWER, DEFAULT_MAX_GRID_POWER, 1000, 50000
        )
        base_grid_setpoint = extractor.get_power_setting(
            CONF_BASE_GRID_SETPOINT, DEFAULT_BASE_GRID_SETPOINT, 1000, 15000
        )
        max_battery_power = extractor.get_power_setting(
            CONF_MAX_BATTERY_POWER, DEFAULT_MAX_BATTERY_POWER, 500, 50000
        )
        max_car_power = extractor.get_power_setting(
            CONF_MAX_CAR_POWER, DEFAULT_MAX_CAR_POWER, 500,
            DEFAULT_SYSTEM_LIMITS.max_car_charger_power * 2
        )
        min_car_threshold = extractor.get_power_setting(
            CONF_MIN_CAR_CHARGING_THRESHOLD, DEFAULT_MIN_CAR_CHARGING_THRESHOLD,
            0, max_car_power
        )
        significant_solar_threshold = extractor.get_power_setting(
            CONF_SIGNIFICANT_SOLAR_THRESHOLD, DEFAULT_SIGNIFICANT_SOLAR_THRESHOLD,
            0, 50000
        )

        # Extract durations and counts
        min_car_duration = extractor.get_int(
            CONF_MIN_CAR_CHARGING_DURATION, DEFAULT_MIN_CAR_CHARGING_DURATION,
            "min_car_charging_duration", minimum=0, maximum=24
        )

        # Extract price adjustments
        price_multiplier = extractor.get_float(
            CONF_PRICE_ADJUSTMENT_MULTIPLIER, DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER,
            "price_adjustment_multiplier"
        )
        price_offset = extractor.get_float(
            CONF_PRICE_ADJUSTMENT_OFFSET, DEFAULT_PRICE_ADJUSTMENT_OFFSET,
            "price_adjustment_offset"
        )
        feed_multiplier = extractor.get_float(
            CONF_FEEDIN_ADJUSTMENT_MULTIPLIER, DEFAULT_FEEDIN_ADJUSTMENT_MULTIPLIER,
            "feedin_adjustment_multiplier"
        )
        feed_offset = extractor.get_float(
            CONF_FEEDIN_ADJUSTMENT_OFFSET, DEFAULT_FEEDIN_ADJUSTMENT_OFFSET,
            "feedin_adjustment_offset"
        )

        # Extract price thresholds
        price_threshold = extractor.get_float(
            CONF_PRICE_THRESHOLD, DEFAULT_PRICE_THRESHOLD, "price_threshold"
        )
        very_low_threshold_pct = extractor.get_float(
            CONF_VERY_LOW_PRICE_THRESHOLD, DEFAULT_VERY_LOW_PRICE_THRESHOLD,
            "very_low_price_threshold"
        )
        very_low_threshold_ratio = max(0.0, min(very_low_threshold_pct / 100.0, 1.0))
        feedin_threshold = extractor.get_float(
            CONF_FEEDIN_PRICE_THRESHOLD, DEFAULT_FEEDIN_PRICE_THRESHOLD,
            "feedin_price_threshold"
        )

        # Extract SOC thresholds
        min_soc_threshold = extractor.get_float(
            CONF_MIN_SOC_THRESHOLD, DEFAULT_MIN_SOC, "min_soc_threshold"
        )
        max_soc_threshold = extractor.get_float(
            CONF_MAX_SOC_THRESHOLD, DEFAULT_MAX_SOC, "max_soc_threshold"
        )
        emergency_soc_threshold = extractor.get_float(
            CONF_EMERGENCY_SOC_THRESHOLD, DEFAULT_EMERGENCY_SOC,
            "emergency_soc_threshold"
        )
        predictive_min_soc = extractor.get_float(
            CONF_PREDICTIVE_CHARGING_MIN_SOC, DEFAULT_PREDICTIVE_CHARGING_MIN_SOC,
            "predictive_charging_min_soc"
        )
        solar_peak_emergency_soc = extractor.get_float(
            CONF_SOLAR_PEAK_EMERGENCY_SOC, DEFAULT_SOLAR_PEAK_EMERGENCY_SOC,
            "solar_peak_emergency_soc"
        )

        # Extract boolean flags
        use_dynamic_threshold = extractor.get_bool(
            CONF_USE_DYNAMIC_THRESHOLD, DEFAULT_USE_DYNAMIC_THRESHOLD
        )
        use_average_threshold = extractor.get_bool(
            CONF_USE_AVERAGE_THRESHOLD, DEFAULT_USE_AVERAGE_THRESHOLD
        )
        dynamic_threshold_confidence = extractor.get_float(
            CONF_DYNAMIC_THRESHOLD_CONFIDENCE, DEFAULT_DYNAMIC_THRESHOLD_CONFIDENCE,
            "dynamic_threshold_confidence"
        )

        return cls(
            max_grid_power=max_grid_power,
            base_grid_setpoint=base_grid_setpoint,
            max_battery_power=max_battery_power,
            max_car_power=max_car_power,
            min_car_charging_threshold=min_car_threshold,
            min_car_charging_duration=min_car_duration,
            price_adjustment_multiplier=price_multiplier,
            price_adjustment_offset=price_offset,
            feedin_adjustment_multiplier=feed_multiplier,
            feedin_adjustment_offset=feed_offset,
            price_threshold=price_threshold,
            very_low_price_threshold_ratio=very_low_threshold_ratio,
            very_low_price_threshold_pct=very_low_threshold_pct,
            feedin_threshold=feedin_threshold,
            significant_solar_threshold=significant_solar_threshold,
            min_soc_threshold=min_soc_threshold,
            max_soc_threshold=max_soc_threshold,
            emergency_soc_threshold=emergency_soc_threshold,
            predictive_min_soc=predictive_min_soc,
            solar_peak_emergency_soc=solar_peak_emergency_soc,
            use_dynamic_threshold=use_dynamic_threshold,
            dynamic_threshold_confidence=dynamic_threshold_confidence,
            use_average_threshold=use_average_threshold,
            battery_capacities=sanitized_capacities,
        )


@dataclass(frozen=True)
class CarDecisionContext:
    """Immutable snapshot of the variables that drive car charging decisions."""

    current_price: Optional[float]
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

    @property
    def display_price(self) -> float:
        """Safe value for string formatting when price is missing."""
        return self.current_price if self.current_price is not None else 0.0

    @property
    def has_allocated_solar(self) -> bool:
        """Whether any solar power is earmarked for the car."""
        return self.allocated_solar > 0

    @property
    def price_category(self) -> PriceCategory:
        """Categorize the current price level for decision routing."""
        if self.very_low_price:
            return PriceCategory.VERY_LOW
        elif self.is_low_price_flag or (self.previous_charging and self.effective_low_price):
            return PriceCategory.LOW
        return PriceCategory.HIGH

    def format_price_comparison(self, operator: str = "≤") -> str:
        """Format price vs threshold comparison for logging."""
        return f"{self.display_price:.3f}€/kWh {operator} {self.effective_threshold:.3f}€/kWh"

    def format_solar_watts(self) -> str:
        """Format allocated solar power as string."""
        return f"{int(self.allocated_solar)}W"


class ChargingDecisionEngine:
    """Engine for making charging decisions based on multiple factors."""

    def __init__(self, hass: HomeAssistant, config: Dict[str, Any]) -> None:
        """Initialize the decision engine."""
        self.hass = hass
        self.config = config
        self.validator = DataValidator()
        self.price_calculator = PriceCalculator()
        self._settings = EngineSettings.from_config(config, self.validator)
        
        # Initialize strategy manager with dynamic threshold configuration
        self.strategy_manager = StrategyManager(
            use_dynamic_threshold=self._settings.use_dynamic_threshold
        )
        
        self.power_validator = PowerAllocationValidator()

    def _get_max_grid_power(self) -> int:
        """Get maximum allowed grid power with safety margin."""
        return self._settings.max_grid_power

    def _get_safe_grid_setpoint(self, monthly_peak: Optional[float]) -> int:
        """Calculate safe grid setpoint based on monthly peak."""
        base_setpoint = self._settings.base_grid_setpoint
        
        if monthly_peak and monthly_peak > base_setpoint:
            return int(monthly_peak * DEFAULT_MONTHLY_PEAK_SAFETY_MARGIN)
        return base_setpoint

    async def evaluate_charging_decision(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluate whether to charge batteries and car from grid based on comprehensive data."""
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

        time_context = TimeContext.get_current_context(
            night_start=DEFAULT_TIME_SCHEDULE.night_start,
            night_end=DEFAULT_TIME_SCHEDULE.night_end,
            solar_peak_start=DEFAULT_TIME_SCHEDULE.solar_peak_start,
            solar_peak_end=DEFAULT_TIME_SCHEDULE.solar_peak_end,
            evening_start=DEFAULT_TIME_SCHEDULE.evening_start,
            evening_end=DEFAULT_TIME_SCHEDULE.evening_end,
        )
        decision_data["time_context"] = time_context
        
        # Allocate solar power
        power_allocation = self._allocate_solar_power(
            power_analysis, battery_analysis, price_analysis, time_context
        )
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
        
        # Make charging decisions
        battery_decision = self._decide_battery_grid_charging(
            price_analysis, battery_analysis, power_allocation, power_analysis, time_context
        )
        decision_data.update(battery_decision)

        # Get dynamic threshold from strategy manager (if dynamic pricing enabled)
        context = {
            "price_analysis": price_analysis,
            "battery_analysis": battery_analysis,
            "power_allocation": power_allocation,
            "power_analysis": power_analysis,
            "time_context": time_context,
            "config": self.config,
            "settings": self._settings,
        }
        dynamic_threshold = self.strategy_manager.get_dynamic_threshold(context)
        if dynamic_threshold is not None:
            price_analysis["dynamic_threshold"] = dynamic_threshold

        car_decision = self._decide_car_grid_charging(
            price_analysis, battery_analysis, power_allocation, data
        )
        decision_data.update(car_decision)
        
        # Calculate power limits
        decision_data_for_downstream = {**data, **decision_data}
        
        charger_limit_decision = self._calculate_charger_limit(
            price_analysis, battery_analysis, power_allocation, decision_data_for_downstream
        )
        decision_data.update(charger_limit_decision)
        decision_data_for_downstream.update(charger_limit_decision)
        
        grid_setpoint_decision = self._calculate_grid_setpoint(
            price_analysis, battery_analysis, power_allocation, decision_data_for_downstream,
            decision_data.get("charger_limit", 0)
        )
        decision_data.update(grid_setpoint_decision)
        
        # Feed-in decision
        feedin_decision = self._decide_feedin_solar(price_analysis, power_allocation)
        decision_data.update(feedin_decision)
        
        return decision_data

    def _initialize_decision_data(self) -> Dict[str, Any]:
        """Initialize the decision data structure."""
        return {
            "battery_grid_charging": False,
            "car_grid_charging": False,
            "battery_grid_charging_reason": "No decision made",
            "car_grid_charging_reason": "No decision made",
            "charger_limit": 0,
            "grid_setpoint": 0,
            "charger_limit_reason": "No decision made",
            "grid_setpoint_reason": "No decision made",
            "feedin_solar": False,
            "feedin_solar_reason": "No decision made",
            "next_evaluation": dt_util.utcnow() + timedelta(minutes=DEFAULT_SYSTEM_LIMITS.evaluation_interval),
            "price_analysis": {},
            "power_analysis": {},
            "battery_analysis": {},
            "solar_analysis": {},
        }

    def _create_no_data_decision(self, decision_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create decision when no price data is available."""
        reason = "No current price data available - all grid charging disabled for safety"
        decision_data["battery_grid_charging_reason"] = reason
        decision_data["car_grid_charging_reason"] = reason
        decision_data["charger_limit_reason"] = "No price data - limiting to solar only"
        decision_data["grid_setpoint_reason"] = "No price data - grid setpoint set to 0"
        decision_data["feedin_solar_reason"] = "No price data - feed-in decision disabled"
        _LOGGER.warning("Critical price data unavailable - disabling all grid charging")
        return decision_data

    def _analyze_comprehensive_pricing(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze comprehensive pricing data from Nord Pool."""
        raw_current_price = data.get("current_price")
        raw_highest_price = data.get("highest_price")
        raw_lowest_price = data.get("lowest_price")
        raw_next_price = data.get("next_price")
        
        price_multiplier = self._settings.price_adjustment_multiplier
        price_offset = self._settings.price_adjustment_offset

        current_price = apply_price_adjustment(raw_current_price, price_multiplier, price_offset)
        highest_price = apply_price_adjustment(raw_highest_price, price_multiplier, price_offset)
        lowest_price = apply_price_adjustment(raw_lowest_price, price_multiplier, price_offset)
        next_price = apply_price_adjustment(raw_next_price, price_multiplier, price_offset)

        transport_cost = data.get("transport_cost") or 0

        if current_price is None:
            _LOGGER.error(
                "Current price unavailable after adjustment (raw=%s, multiplier=%s, offset=%s) - "
                "disabling charging decisions for safety",
                raw_current_price, price_multiplier, price_offset
            )
            return self._create_unavailable_price_analysis(
                raw_highest_price, raw_lowest_price, raw_next_price,
                self._settings.price_threshold,
                transport_cost,
            )

        # Add transport cost to all prices
        adjusted_prices = self._add_transport_cost_to_prices(
            {
                "current_price": current_price,
                "highest_price": highest_price,
                "lowest_price": lowest_price,
                "next_price": next_price,
            },
            transport_cost
        )
        current_price = adjusted_prices["current_price"]
        highest_price = adjusted_prices["highest_price"]
        lowest_price = adjusted_prices["lowest_price"]
        next_price = adjusted_prices["next_price"]

        # Determine which threshold to use
        use_average_threshold = self._settings.use_average_threshold
        average_threshold = data.get("average_threshold")

        if use_average_threshold and average_threshold is not None:
            price_threshold = average_threshold
        else:
            price_threshold = self._settings.price_threshold

        very_low_threshold = self._settings.very_low_price_threshold_ratio

        # Use cached price position calculation
        price_position = self.price_calculator.calculate_price_position(
            current_price, highest_price or current_price, lowest_price or current_price
        )
        
        # Check price trends
        next_price_higher = next_price is not None and next_price > current_price
        price_trend_improving = next_price is not None and next_price < current_price
        significant_price_drop = self.price_calculator.is_significant_price_drop(
            current_price, next_price, DEFAULT_ALGORITHM_THRESHOLDS.significant_price_drop
        )
        
        return {
            "current_price": current_price,
            "highest_price": highest_price,
            "lowest_price": lowest_price,
            "next_price": next_price,
            "raw_current_price": raw_current_price,
            "raw_highest_price": raw_highest_price,
            "raw_lowest_price": raw_lowest_price,
            "raw_next_price": raw_next_price,
            "price_adjustment_multiplier": price_multiplier,
            "price_adjustment_offset": price_offset,
            "transport_cost": transport_cost,
            "price_threshold": price_threshold,
            "is_low_price": current_price <= price_threshold,
            "is_lowest_price": lowest_price is not None and current_price == lowest_price,
            "price_position": price_position,
            "next_price_higher": next_price_higher,
            "price_trend_improving": price_trend_improving,
            "significant_price_drop": significant_price_drop,
            "very_low_price": price_position <= very_low_threshold,
            "data_available": True,
        }

    def _add_transport_cost_to_prices(
        self,
        prices: Dict[str, Optional[float]],
        transport_cost: float,
    ) -> Dict[str, Optional[float]]:
        """Add transport cost to all non-None prices."""
        return {
            key: (price + transport_cost if price is not None else None)
            for key, price in prices.items()
        }

    def _create_unavailable_price_analysis(
        self,
        highest_price: Optional[float],
        lowest_price: Optional[float],
        next_price: Optional[float],
        price_threshold: float,
        transport_cost: float = 0.0,
    ) -> Dict[str, Any]:
        """Create price analysis when current price is unavailable."""
        return {
            "current_price": None,
            "highest_price": highest_price,
            "lowest_price": lowest_price,
            "next_price": next_price,
            "price_threshold": price_threshold,
            "is_low_price": False,
            "is_lowest_price": False,
            "price_position": None,
            "next_price_higher": False,
            "price_trend_improving": False,
            "significant_price_drop": False,
            "very_low_price": False,
            "data_available": False,
            "raw_current_price": None,
            "raw_highest_price": highest_price,
            "raw_lowest_price": lowest_price,
            "raw_next_price": next_price,
            "price_adjustment_multiplier": self._settings.price_adjustment_multiplier,
            "price_adjustment_offset": self._settings.price_adjustment_offset,
            "transport_cost": transport_cost,
        }

    def _analyze_power_flow(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze current power flow and consumption."""
        solar_production = data.get("solar_production", 0) or 0
        house_consumption = data.get("house_consumption", 0) or 0
        solar_surplus = data.get("solar_surplus", 0) or 0
        car_charging_power = data.get("car_charging_power", 0) or 0
        
        # Validate power values
        solar_production = self.validator.validate_power_value(
            solar_production, name="solar_production", max_value=50000
        )
        house_consumption = self.validator.validate_power_value(
            house_consumption, name="house_consumption", max_value=50000
        )
        car_charging_power = self.validator.validate_power_value(
            car_charging_power, name="car_charging", max_value=22000
        )
        
        significant_solar_threshold = self._settings.significant_solar_threshold
        
        house_consumption_without_car = max(0, house_consumption - car_charging_power)
        solar_coverage_ratio = (solar_production / house_consumption) if house_consumption > 0 else 0
        
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
            "available_surplus_for_batteries": max(0, solar_surplus - car_charging_power),
            "significant_solar_threshold": significant_solar_threshold,
            "solar_coverage_ratio": solar_coverage_ratio,
            "has_excess_solar_available": solar_surplus > 0,
        }

    def _analyze_solar_production(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze solar production status."""
        solar_production = data.get("solar_production", 0) or 0
        house_consumption = data.get("house_consumption", 0) or 0
        solar_surplus = data.get("solar_surplus", 0) or 0
        
        is_producing = solar_production > 0
        has_available_surplus = solar_surplus > 0
        production_efficiency = (
            min(1.0, solar_production / DEFAULT_POWER_ESTIMATES.max_solar_production)
            if solar_production > 0 else 0
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
        power_analysis: Dict[str, Any],
        battery_analysis: Dict[str, Any],
        price_analysis: Dict[str, Any],
        time_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Hierarchically allocate solar surplus."""
        solar_surplus = power_analysis.get("solar_surplus", 0)
        significant_solar_threshold = self._settings.significant_solar_threshold
        
        solar_surplus = self.validator.validate_power_value(solar_surplus, name="solar_surplus")
        
        # Handle insufficient solar
        if solar_surplus <= significant_solar_threshold:
            return self._create_insufficient_solar_allocation(
                solar_surplus, power_analysis, significant_solar_threshold
            )
        
        # Track car's current solar usage
        car_current_solar_usage = self._calculate_car_solar_usage(
            power_analysis, solar_surplus, self._settings
        )
        
        # Available solar after current car consumption
        available_solar = max(0, solar_surplus - car_current_solar_usage)
        
        # Allocate to batteries
        solar_for_batteries = self._calculate_battery_solar_allocation(
            available_solar, battery_analysis, significant_solar_threshold, self._settings
        )
        available_solar = max(0, available_solar - solar_for_batteries)
        
        # Allocate remaining to car if batteries are near full
        solar_for_car = self._calculate_car_solar_allocation(
            available_solar, battery_analysis, self._settings
        )
        available_solar = max(0, available_solar - solar_for_car)
        
        # Validate and scale if needed
        total_allocated = solar_for_batteries + solar_for_car + car_current_solar_usage
        if total_allocated > solar_surplus:
            scale_factor = solar_surplus / total_allocated if total_allocated > 0 else 0
            solar_for_batteries = int(solar_for_batteries * scale_factor)
            solar_for_car = int(solar_for_car * scale_factor)
            car_current_solar_usage = int(car_current_solar_usage * scale_factor)
            _LOGGER.warning(
                "Power allocation %dW exceeds available solar %dW - scaled down",
                total_allocated, solar_surplus
            )
        
        remaining_solar = max(0, solar_surplus - solar_for_batteries - solar_for_car - car_current_solar_usage)
        
        return {
            "solar_for_batteries": solar_for_batteries,
            "solar_for_car": solar_for_car,
            "car_current_solar_usage": car_current_solar_usage,
            "remaining_solar": remaining_solar,
            "total_allocated": solar_for_batteries + solar_for_car + car_current_solar_usage,
            "allocation_reason": format_reason(
                "Power allocation",
                f"Car using {car_current_solar_usage}W",
                {
                    "batteries": f"{solar_for_batteries}W",
                    "car_additional": f"{solar_for_car}W",
                    "remaining": f"{remaining_solar}W",
                    "total": f"{solar_surplus}W"
                }
            )
        }

    def _create_insufficient_solar_allocation(
        self,
        solar_surplus: float,
        power_analysis: Dict[str, Any],
        threshold: float
    ) -> Dict[str, Any]:
        """Create allocation for insufficient solar."""
        car_charging_power = power_analysis.get("car_charging_power", 0)
        min_car_threshold = self._settings.min_car_charging_threshold
        
        car_current_solar_usage = 0
        if car_charging_power > min_car_threshold:
            car_current_solar_usage = min(car_charging_power, solar_surplus)
        
        remaining_solar = max(0, solar_surplus - car_current_solar_usage)
        
        return {
            "solar_for_batteries": 0,
            "solar_for_car": 0,
            "car_current_solar_usage": car_current_solar_usage,
            "remaining_solar": remaining_solar,
            "total_allocated": car_current_solar_usage,
            "allocation_reason": f"Insufficient solar ({solar_surplus}W ≤ {threshold}W) - car using {car_current_solar_usage}W, {remaining_solar}W remaining"
        }

    def _calculate_car_solar_usage(
        self,
        power_analysis: Dict[str, Any],
        solar_surplus: float,
        settings: EngineSettings,
    ) -> int:
        """Calculate how much solar the car is currently using."""
        car_charging_power = power_analysis.get("car_charging_power", 0)
        min_threshold = settings.min_car_charging_threshold
        
        if car_charging_power > min_threshold and solar_surplus > 0:
            return min(car_charging_power, solar_surplus)
        return 0

    def _calculate_battery_solar_allocation(
        self,
        available_solar: float,
        battery_analysis: Dict[str, Any],
        significant_threshold: float,
        settings: EngineSettings,
    ) -> int:
        """Calculate solar allocation for batteries."""
        batteries_full = battery_analysis.get("batteries_full", False)
        average_soc = battery_analysis.get("average_soc")
        max_soc = battery_analysis.get("max_soc_threshold", DEFAULT_MAX_SOC)
        
        if average_soc is None or max_soc is None:
            return 0
        
        if (not batteries_full and
            average_soc < max_soc - DEFAULT_ALGORITHM_THRESHOLDS.soc_safety_margin and
            available_solar > 0):
            
            soc_deficit = max(0, max_soc - average_soc)
            estimated_need = min(
                available_solar,
                int(soc_deficit * DEFAULT_POWER_ESTIMATES.per_soc_percent),
                significant_threshold,
                settings.max_battery_power
            )
            return max(0, estimated_need)
        return 0

    def _calculate_car_solar_allocation(
        self,
        available_solar: float,
        battery_analysis: Dict[str, Any],
        settings: EngineSettings,
    ) -> int:
        """Calculate solar allocation for car."""
        if available_solar <= 0:
            return 0
        
        max_soc = battery_analysis.get("max_soc_threshold", DEFAULT_MAX_SOC)
        average_soc = battery_analysis.get("average_soc")
        min_soc = battery_analysis.get("min_soc")
        batteries_full = battery_analysis.get("batteries_full", False)
        
        # Treat solar as a bonus: only allocate it to the car when every battery is already near full.
        solar_ready_threshold = max_soc - DEFAULT_ALGORITHM_THRESHOLDS.soc_buffer
        if batteries_full:
            return min(
                available_solar,
                settings.max_car_power
            )
        
        if average_soc is None or min_soc is None:
            return 0
        
        if average_soc >= solar_ready_threshold and min_soc >= solar_ready_threshold:
            return min(
                available_solar,
                settings.max_car_power
            )
        return 0

    def _analyze_battery_status(self, battery_soc_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze battery status for all configured batteries."""
        # Validate battery data
        is_valid, validation_msg = self.validator.validate_battery_data(battery_soc_data)
        
        if not battery_soc_data:
            return self._create_no_battery_result()
        
        # Filter valid batteries
        valid_batteries = [
            battery for battery in battery_soc_data
            if "soc" in battery and battery["soc"] is not None
        ]
        
        if not valid_batteries:
            return self._create_unavailable_battery_result(len(battery_soc_data))
        
        # Calculate metrics
        soc_values = [battery["soc"] for battery in valid_batteries]
        min_soc = min(soc_values)
        max_soc = max(soc_values)
        
        # Calculate weighted average if capacities configured
        average_soc = self._calculate_weighted_average_soc(valid_batteries)
        
        min_threshold = self._settings.min_soc_threshold
        max_threshold = self._settings.max_soc_threshold
        
        return {
            "average_soc": average_soc,
            "min_soc": min_soc,
            "max_soc": max_soc,
            "batteries_count": len(soc_values),
            "batteries_full": min_soc >= max_threshold,
            "min_soc_threshold": min_threshold,
            "max_soc_threshold": max_threshold,
            "remaining_capacity_percent": max_threshold - average_soc,
            "batteries_available": True,
            "validation_status": validation_msg,
            "capacity_weighted": bool(self._settings.battery_capacities),
        }

    def _create_no_battery_result(self) -> Dict[str, Any]:
        """Create result when no batteries configured."""
        return {
            "average_soc": None,
            "min_soc": None,
            "max_soc": None,
            "batteries_count": 0,
            "batteries_full": False,
            "batteries_available": False,
            "validation_status": "No battery entities configured",
        }

    def _create_unavailable_battery_result(self, count: int) -> Dict[str, Any]:
        """Create result when all batteries unavailable."""
        return {
            "average_soc": None,
            "min_soc": None,
            "max_soc": None,
            "batteries_count": count,
            "batteries_full": False,
            "batteries_available": False,
            "validation_status": "All battery SOC sensors unavailable",
        }

    def _calculate_weighted_average_soc(self, batteries: List[Dict[str, Any]]) -> float:
        """Calculate capacity-weighted average SOC."""
        capacities = self._settings.battery_capacities
        
        if not capacities:
            # Simple average
            return sum(b["soc"] for b in batteries) / len(batteries)
        
        # Weighted average
        total_energy = 0.0
        total_capacity = 0.0
        
        for battery in batteries:
            entity_id = battery["entity_id"]
            soc = battery["soc"]
            capacity = capacities.get(entity_id, DEFAULT_POWER_ESTIMATES.default_battery_capacity)
            
            energy = (soc / 100.0) * capacity
            total_energy += energy
            total_capacity += capacity
            
            _LOGGER.debug(
                "Battery %s: SOC=%.1f%%, Capacity=%.1fkWh, Stored=%.2fkWh",
                entity_id, soc, capacity, energy
            )
        
        if total_capacity > 0:
            return (total_energy / total_capacity) * 100.0
        
        return sum(b["soc"] for b in batteries) / len(batteries)

    def _decide_battery_grid_charging(
        self,
        price_analysis: Dict[str, Any],
        battery_analysis: Dict[str, Any],
        power_allocation: Dict[str, Any],
        power_analysis: Dict[str, Any],
        time_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Decide battery charging using strategy pattern."""
        # Safety checks
        if battery_analysis.get("batteries_count", 0) == 0:
            return {
                "battery_grid_charging": False,
                "battery_grid_charging_reason": "No battery entities configured",
                "strategy_trace": [],
            }

        if not battery_analysis.get("batteries_available", True):
            return {
                "battery_grid_charging": False,
                "battery_grid_charging_reason": battery_analysis.get(
                    "validation_status",
                    "Battery data unavailable"
                ),
                "strategy_trace": [],
            }

        if battery_analysis.get("batteries_full"):
            max_threshold = battery_analysis.get("max_soc_threshold", 90)
            return {
                "battery_grid_charging": False,
                "battery_grid_charging_reason": f"Batteries above {max_threshold}% SOC",
                "strategy_trace": [],
            }

        if not price_analysis.get("data_available", True):
            return {
                "battery_grid_charging": False,
                "battery_grid_charging_reason": "No price data available",
                "strategy_trace": [],
            }

        # Early return: If significant solar available + medium/high SOC
        # → Skip grid charging and wait for solar instead (always prefer free solar)
        significant_solar = power_analysis.get("significant_solar_surplus", False)
        solar_surplus = power_analysis.get("solar_surplus", 0)
        average_soc = battery_analysis.get("average_soc")
        surplus_block_soc = DEFAULT_ALGORITHM_THRESHOLDS.medium_soc_threshold

        if (
            significant_solar
            and average_soc is not None
            and average_soc >= surplus_block_soc
        ):
            return {
                "battery_grid_charging": False,
                "battery_grid_charging_reason": (
                    f"Significant solar surplus ({solar_surplus:.0f}W) available - "
                    f"SOC {average_soc:.0f}% ≥ {surplus_block_soc}% so waiting for free solar "
                    f"(even at very low prices)"
                ),
                "strategy_trace": [],
            }

        # Create context for strategies
        context = {
            "price_analysis": price_analysis,
            "battery_analysis": battery_analysis,
            "power_allocation": power_allocation,
            "power_analysis": power_analysis,
            "time_context": time_context,
            "config": self.config,
        }

        # Use strategy manager
        should_charge, reason = self.strategy_manager.evaluate(context)
        trace = self.strategy_manager.get_last_trace()

        return {
            "battery_grid_charging": should_charge,
            "battery_grid_charging_reason": reason,
            "strategy_trace": trace,
        }

    def _decide_car_grid_charging(
        self,
        price_analysis: Dict[str, Any],
        battery_analysis: Dict[str, Any],
        power_allocation: Dict[str, Any],
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Decide whether to charge car from grid with hysteresis.

        Hysteresis logic:
        - OFF → ON: Only if price is low AND we have minimum hours of low prices ahead (configurable)
        - ON → OFF: Only if price exceeds threshold (continues charging during low prices)

        This prevents frequent on/off switching for short low-price periods.
        """
        if not price_analysis.get("data_available", True):
            return {
                "car_grid_charging": False,
                "car_grid_charging_reason": "No price data available",
            }

        context = self._build_car_decision_context(price_analysis, power_allocation, data)

        if context.very_low_price:
            return self._car_decision_for_very_low_price(context, data)

        if context.is_low_price_flag or (context.previous_charging and context.effective_low_price):
            return self._car_decision_for_low_price(context, data)

        return self._car_decision_for_high_price(context, data)

    def _decide_feedin_solar(
        self,
        price_analysis: Dict[str, Any],
        power_allocation: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Decide whether to enable solar feed-in."""
        if not price_analysis.get("data_available", True):
            return {
                "feedin_solar": False,
                "feedin_solar_reason": "No price data available",
                "feedin_effective_price": None,
            }
        
        current_price = price_analysis.get("current_price")
        raw_price = price_analysis.get("raw_current_price", current_price)

        if current_price is None:
            return {
                "feedin_solar": False,
                "feedin_solar_reason": "No adjusted price available for feed-in",
                "feedin_effective_price": None,
            }
        
        feed_multiplier = self._settings.feedin_adjustment_multiplier
        feed_offset = self._settings.feedin_adjustment_offset
        feedin_threshold = self._settings.feedin_threshold
        remaining_solar = power_allocation.get("remaining_solar", 0)
        
        adjustments_active = (
            feed_multiplier != DEFAULT_FEEDIN_ADJUSTMENT_MULTIPLIER
            or feed_offset != DEFAULT_FEEDIN_ADJUSTMENT_OFFSET
        )
        adjusted_feed_price = apply_price_adjustment(raw_price, feed_multiplier, feed_offset)
        if adjusted_feed_price is None:
            adjusted_feed_price = current_price
        
        if adjustments_active:
            effective_threshold = feedin_threshold
            enable_feedin = adjusted_feed_price >= effective_threshold
            comparator = "≥" if enable_feedin else "<"
            action = "enable" if enable_feedin else "disable"
            reason = (
                f"Net feed-in price {adjusted_feed_price:.3f}€/kWh {comparator} "
                f"{effective_threshold:.3f}€/kWh - {action} solar export "
                f"(surplus: {remaining_solar}W)"
            )
        else:
            enable_feedin = current_price >= feedin_threshold
            comparator = "≥" if enable_feedin else "<"
            action = "enable" if enable_feedin else "disable"
            reason = (
                f"Price {current_price:.3f}€/kWh {comparator} {feedin_threshold:.3f}€/kWh - "
                f"{action} solar export (surplus: {remaining_solar}W)"
            )
        
        return {
            "feedin_solar": enable_feedin,
            "feedin_solar_reason": reason,
            "feedin_effective_price": adjusted_feed_price,
        }

    def _build_car_decision_context(
        self,
        price_analysis: Dict[str, Any],
        power_allocation: Dict[str, Any],
        data: Dict[str, Any],
    ) -> CarDecisionContext:
        """Collect immutable inputs for car charging decision making."""
        base_threshold_raw = price_analysis.get("price_threshold")
        base_threshold = (
            float(base_threshold_raw)
            if base_threshold_raw is not None
            else float(self._settings.price_threshold)
        )

        previous_car_charging = bool(data.get(_PREVIOUS_CAR_CHARGING_KEY))
        locked_threshold = _safe_optional_float(data.get(_CAR_CHARGING_LOCKED_THRESHOLD_KEY))
        effective_threshold = self._resolve_car_threshold(
            base_threshold, locked_threshold, previous_car_charging
        )

        current_price = price_analysis.get("current_price")

        allocated_solar = _safe_optional_float(power_allocation.get("solar_for_car")) or 0.0

        return CarDecisionContext(
            current_price=current_price,
            base_threshold=base_threshold,
            effective_threshold=effective_threshold,
            previous_charging=previous_car_charging,
            has_min_window=bool(data.get(_HAS_MIN_CHARGING_WINDOW_KEY)),
            min_duration=self._settings.min_car_charging_duration,
            allocated_solar=allocated_solar,
            very_low_price=bool(price_analysis.get("very_low_price")),
            very_low_percent=float(self._settings.very_low_price_threshold_pct),
            is_low_price_flag=bool(price_analysis.get("is_low_price")),
            effective_low_price=(
                current_price is not None and current_price <= effective_threshold
            ),
        )

    def _resolve_car_threshold(
        self,
        current_threshold: float,
        locked_threshold: Optional[float],
        previous_car_charging: bool,
    ) -> float:
        """Apply hysteresis threshold floor while the car is charging."""
        if not previous_car_charging or locked_threshold is None:
            return current_threshold

        threshold = max(locked_threshold, current_threshold)
        _LOGGER.debug(
            "Car charging active: using threshold floor %.4f€/kWh (locked=%.4f€/kWh, current=%.4f€/kWh)",
            threshold,
            locked_threshold,
            current_threshold,
        )
        return threshold

    def _lock_car_charging_threshold(
        self,
        context: CarDecisionContext,
        data: Dict[str, Any],
    ) -> None:
        """Lock the price threshold when starting car charging (OFF→ON transition)."""
        data[_CAR_CHARGING_LOCKED_THRESHOLD_KEY] = context.base_threshold
        _LOGGER.debug(
            "Car charging starting: locking threshold at %.4f€/kWh",
            context.base_threshold,
        )

    def _unlock_car_charging_threshold(self, data: Dict[str, Any]) -> None:
        """Clear the locked threshold when stopping car charging (ON→OFF transition)."""
        data[_CAR_CHARGING_LOCKED_THRESHOLD_KEY] = None
        _LOGGER.debug("Car charging stopping: clearing locked threshold")

    def _append_solar_info_to_reason(
        self,
        reason: str,
        context: CarDecisionContext,
    ) -> str:
        """Append solar allocation info to reason string if solar is allocated."""
        if context.has_allocated_solar:
            return f"{reason}, solar available ({context.format_solar_watts()})"
        return reason

    def _build_reason_with_solar(
        self,
        base_reason: str,
        context: CarDecisionContext,
        include_solar_inline: bool = False,
    ) -> str:
        """Build charging reason with optional solar allocation details."""
        if include_solar_inline and context.has_allocated_solar:
            return f"{base_reason} with solar ({context.format_solar_watts()})"
        elif context.has_allocated_solar:
            return self._append_solar_info_to_reason(base_reason, context)
        return base_reason

    def _car_decision_for_very_low_price(
        self,
        context: CarDecisionContext,
        data: Dict[str, Any],
    ) -> CarChargingDecision:
        """Handle very low price cases."""
        price = context.display_price

        if context.previous_charging:
            reason = (
                f"Very low price ({price:.3f}€/kWh) - bottom "
                f"{context.very_low_percent}% of daily range (continuing)"
            )
        elif context.has_min_window:
            self._lock_car_charging_threshold(context, data)
            reason = (
                f"Very low price ({price:.3f}€/kWh) - bottom "
                f"{context.very_low_percent}% of daily range ({context.min_duration}h+ window available)"
            )
        else:
            return {
                "car_grid_charging": False,
                "car_grid_charging_reason": (
                    f"Very low price ({price:.3f}€/kWh) but less than {context.min_duration}h "
                    "of low prices ahead - waiting for longer window"
                ),
            }

        reason = self._append_solar_info_to_reason(reason, context)
        return {
            "car_grid_charging": True,
            "car_grid_charging_reason": reason,
        }

    def _car_decision_for_low_price(
        self,
        context: CarDecisionContext,
        data: Dict[str, Any],
    ) -> CarChargingDecision:
        """Handle regular low price cases with hysteresis."""
        if context.previous_charging:
            base_reason = f"Low price ({context.format_price_comparison()}) - continuing"
            reason = self._build_reason_with_solar(base_reason, context, include_solar_inline=True)
            return {
                "car_grid_charging": True,
                "car_grid_charging_reason": reason,
            }

        if context.has_min_window:
            self._lock_car_charging_threshold(context, data)
            base_reason = (
                f"Low price ({context.format_price_comparison()}), "
                f"{context.min_duration}h+ window available - starting"
            )
            reason = self._build_reason_with_solar(base_reason, context, include_solar_inline=True)
            return {
                "car_grid_charging": True,
                "car_grid_charging_reason": reason,
            }

        if context.is_low_price_flag:
            return {
                "car_grid_charging": False,
                "car_grid_charging_reason": (
                    f"Low price ({context.format_price_comparison()}) but less than {context.min_duration}h "
                    "of low prices ahead - waiting for longer window"
                ),
            }

        return {
            "car_grid_charging": False,
            "car_grid_charging_reason": (
                f"Waiting for low-price flag before starting ({context.format_price_comparison()} floor, "
                f"threshold currently {context.base_threshold:.3f}€/kWh)"
            ),
        }

    def _car_decision_for_high_price(
        self,
        context: CarDecisionContext,
        data: Dict[str, Any],
    ) -> CarChargingDecision:
        """Handle high price cases where charging should pause or fall back to solar."""
        if context.previous_charging:
            self._unlock_car_charging_threshold(data)
            return {
                "car_grid_charging": False,
                "car_grid_charging_reason": (
                    f"Price exceeded threshold ({context.format_price_comparison('>')}) - "
                    "stopping car charging"
                ),
            }

        if context.has_allocated_solar:
            return {
                "car_grid_charging": True,
                "car_solar_only": True,
                "car_grid_charging_reason": (
                    f"Price too high ({context.format_price_comparison('>')}) - "
                    f"using allocated solar power only ({context.format_solar_watts()})"
                ),
            }

        return {
            "car_grid_charging": False,
            "car_grid_charging_reason": f"Price too high ({context.format_price_comparison('>')})",
        }

    def _calculate_charger_limit(
        self,
        price_analysis: Dict[str, Any],
        battery_analysis: Dict[str, Any],
        power_allocation: Dict[str, Any],
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Calculate optimal charger power limit."""
        car_charging_power = data.get("car_charging_power", 0)
        min_threshold = self._settings.min_car_charging_threshold
        car_limit_cap = min(self._settings.max_car_power, DEFAULT_SYSTEM_LIMITS.max_car_charger_power)

        if car_charging_power <= min_threshold:
            return {
                "charger_limit": 0,
                "charger_limit_reason": "Car not currently charging",
            }
        
        # Check if car charging is allowed
        car_grid_charging = data.get("car_grid_charging", False)
        
        if not car_grid_charging:
            return {
                "charger_limit": 1400,
                "charger_limit_reason": "Car charging not allowed - limited to 1.4kW, battery usage still allowed",
            }
        
        # Handle solar-only charging
        car_solar_only = data.get("car_solar_only", False)
        allocated_solar = power_allocation.get("solar_for_car", 0)
        
        if car_solar_only and allocated_solar > 0:
            limit = min(allocated_solar, car_limit_cap)
            return {
                "charger_limit": int(limit),
                "charger_limit_reason": f"Solar-only car charging - limited to allocated solar power ({int(limit)}W), no grid usage",
            }
        
        # Calculate based on battery SOC and grid limits
        average_soc = battery_analysis.get("average_soc")
        
        if average_soc is None:
            if not car_grid_charging:
                return {
                    "charger_limit": 1400,
                    "charger_limit_reason": "Battery data unavailable, car charging not allowed - limited to 1.4kW",
                }
            monthly_peak = data.get("monthly_grid_peak", 0)
            max_setpoint = self._get_safe_grid_setpoint(monthly_peak)
            limit = min(max_setpoint, car_limit_cap)
            return {
                "charger_limit": int(limit),
                "charger_limit_reason": f"Battery data unavailable - conservative limit ({int(limit)}W)",
            }
        
        max_soc_threshold = battery_analysis.get("max_soc_threshold", 80)
        monthly_peak = data.get("monthly_grid_peak", 0)
        max_setpoint = self._get_safe_grid_setpoint(monthly_peak)

        if average_soc < max_soc_threshold:
            limit = min(max_setpoint, car_limit_cap)
            return {
                "charger_limit": int(limit),
                "charger_limit_reason": (f"Battery {average_soc:.0f}% < {max_soc_threshold}% - "
                                        f"car limited to grid setpoint ({int(limit)}W), surplus for batteries"),
            }
        
        available_surplus = power_allocation.get("remaining_solar", 0)
        available_power = available_surplus + max_setpoint
        limit = min(available_power, car_limit_cap)
        
        return {
            "charger_limit": int(limit),
            "charger_limit_reason": (f"Battery {average_soc:.0f}% ≥ {max_soc_threshold}% - "
                                    f"car can use remaining surplus + grid ({int(limit)}W = "
                                    f"{available_surplus}W + {max_setpoint}W)"),
        }

    def _calculate_grid_setpoint(
        self,
        price_analysis: Dict[str, Any],
        battery_analysis: Dict[str, Any],
        power_allocation: Dict[str, Any],
        data: Dict[str, Any],
        charger_limit: int,
    ) -> Dict[str, Any]:
        """Calculate grid setpoint based on energy management scenario."""
        car_charging_power = data.get("car_charging_power", 0)
        battery_grid_charging = data.get("battery_grid_charging", False)
        car_grid_charging = data.get("car_grid_charging", False)
        average_soc = battery_analysis.get("average_soc")

        min_threshold = self._settings.min_car_charging_threshold
        significant_car_charging = car_charging_power >= min_threshold
        
        # Handle unavailable battery data
        if average_soc is None:
            if significant_car_charging and car_grid_charging:
                monthly_peak = data.get("monthly_grid_peak", 0)
                max_setpoint = self._get_safe_grid_setpoint(monthly_peak)
                grid_setpoint = min(car_charging_power, max_setpoint)
                return {
                    "grid_setpoint": int(grid_setpoint),
                    "grid_setpoint_reason": f"Battery data unavailable - grid only for car ({int(grid_setpoint)}W)",
                }
            return {
                "grid_setpoint": 0,
                "grid_setpoint_reason": "Battery data unavailable - no grid power allocated",
            }
        
        # Solar-only car charging
        car_solar_only = data.get("car_solar_only", False)
        if significant_car_charging and car_solar_only:
            return {
                "grid_setpoint": 0,
                "grid_setpoint_reason": "Solar-only car charging detected - grid setpoint 0W",
            }
        
        # Calculate grid needs
        monthly_peak = data.get("monthly_grid_peak", 0)
        max_setpoint = self._get_safe_grid_setpoint(monthly_peak)
        
        grid_setpoint_parts = []
        car_grid_need = 0
        
        if significant_car_charging and car_grid_charging:
            allocated_solar = power_allocation.get("solar_for_car", 0)
            car_current_solar = power_allocation.get("car_current_solar_usage", 0)
            car_available_solar = allocated_solar + car_current_solar
            car_grid_need = max(0, min(car_charging_power - car_available_solar, max_setpoint))
            if car_grid_need > 0:
                grid_setpoint_parts.append(f"car {int(car_grid_need)}W")
        
        battery_grid_need = 0
        if battery_grid_charging:
            remaining_capacity = max(0, max_setpoint - car_grid_need)
            max_battery_power = self._settings.max_battery_power
            battery_grid_need = min(remaining_capacity, max_battery_power)
            if battery_grid_need > 0:
                grid_setpoint_parts.append(f"battery {int(battery_grid_need)}W")
        
        grid_setpoint = car_grid_need + battery_grid_need
        max_grid_power = self._get_max_grid_power()
        grid_setpoint = min(grid_setpoint, max_setpoint, max_grid_power)
        
        # Create reason
        if not grid_setpoint_parts:
            reason = "No grid charging needed"
        else:
            reason = f"Grid setpoint for {' + '.join(grid_setpoint_parts)} = {int(grid_setpoint)}W"
            if car_grid_need == 0 and significant_car_charging:
                reason += " (car charging not allowed)"
        
        return {
            "grid_setpoint": int(grid_setpoint),
            "grid_setpoint_reason": reason,
        }
