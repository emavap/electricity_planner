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
    CONF_PHASE_MODE,
    PHASE_MODE_SINGLE,
    PHASE_MODE_THREE,
    PHASE_IDS,
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
    CONF_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
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
    DEFAULT_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
    DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER,
    DEFAULT_PRICE_ADJUSTMENT_OFFSET,
    DEFAULT_FEEDIN_ADJUSTMENT_MULTIPLIER,
    DEFAULT_FEEDIN_ADJUSTMENT_OFFSET,
    DEFAULT_MONTHLY_PEAK_SAFETY_MARGIN,
    MAX_CAR_CHARGER_POWER,
    EVALUATION_INTERVAL_MINUTES,
    SOC_SAFETY_MARGIN,
    DEFAULT_BATTERY_CAPACITY_KWH,
)

from .defaults import (
    DEFAULT_TIME_SCHEDULE,
    DEFAULT_POWER_ESTIMATES,
    DEFAULT_ALGORITHM_THRESHOLDS,
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
    car_permissive_threshold_multiplier: float
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
            MAX_CAR_CHARGER_POWER * 2
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

        # Extract car permissive mode multiplier
        car_permissive_multiplier = extractor.get_float(
            CONF_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER, DEFAULT_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
            "car_permissive_threshold_multiplier"
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
            car_permissive_threshold_multiplier=car_permissive_multiplier,
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

    async def evaluate_charging_decision(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluate whether to charge batteries and car from grid."""
        phase_mode = data.get(CONF_PHASE_MODE)
        if phase_mode == PHASE_MODE_THREE:
            return await self._evaluate_three_phase(data)
        return await self._evaluate_single_phase(data)

    async def _evaluate_single_phase(self, data: Dict[str, Any]) -> Dict[str, Any]:
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

    async def _evaluate_three_phase(self, data: Dict[str, Any]) -> Dict[str, Any]:
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
        overall_decision: Dict[str, Any],
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Break down the aggregated decision into per-phase guidance.

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
        phase_details: Dict[str, Dict[str, Any]] = data.get("phase_details") or {}
        if not phase_details:
            _LOGGER.warning(
                "Three-phase mode active but no phase details available - "
                "check that at least one sensor is configured for each phase"
            )
            # Return overall decision without phase breakdown
            return {}

        ordered_phases = [phase for phase in PHASE_IDS if phase in phase_details]
        if not ordered_phases:
            ordered_phases = list(phase_details.keys())

        phase_capacity_map: Dict[str, float] = data.get("phase_capacity_map", {})
        phase_batteries: Dict[str, List[Dict[str, Any]]] = data.get("phase_batteries", {})
        total_capacity_weight = sum(
            max(phase_capacity_map.get(phase, 0.0), 0.0) for phase in ordered_phases
        )

        grid_components = overall_decision.get("grid_components") or {}
        total_grid_setpoint = int(overall_decision.get("grid_setpoint", 0) or 0)
        battery_component = grid_components.get("battery")
        car_component = grid_components.get("car")

        if battery_component is None:
            battery_component = total_grid_setpoint if overall_decision.get("battery_grid_charging") else 0
        if car_component is None:
            car_component = max(0, total_grid_setpoint - int(battery_component))

        battery_component = int(battery_component or 0)
        car_component = int(car_component or 0)

        # Determine capacity-weighted distribution for battery power
        # Battery power is allocated proportionally based on each phase's
        # share of total battery capacity (kWh)
        battery_phases = [
            phase for phase in ordered_phases if phase_batteries.get(phase)
        ]
        battery_weight_map = {
            phase: max(phase_capacity_map.get(phase, 0.0), 0.0)
            for phase in ordered_phases
        }
        if not battery_phases and battery_component > 0:
            battery_phases = ordered_phases

        battery_allocations = (
            self._distribute_quantity(
                battery_component,
                battery_phases,
                {phase: battery_weight_map.get(phase, 0.0) for phase in battery_phases},
            )
            if battery_component > 0 and battery_phases
            else {phase: 0 for phase in ordered_phases}
        )

        # Determine weighting for car component
        # Car power is distributed equally across phases with car sensors (not by current draw)
        car_weight_map: Dict[str, float] = {}
        car_phases: List[str] = []
        for phase in ordered_phases:
            details = phase_details.get(phase, {})
            car_power = details.get("car_charging_power")
            has_car = details.get("has_car_sensor") or (car_power is not None)
            if has_car:
                car_phases.append(phase)
                car_weight_map[phase] = 1.0  # Equal weight for all car phases

        if car_component > 0 and not car_phases:
            car_phases = ordered_phases
            car_weight_map = {phase: 1.0 for phase in ordered_phases}

        car_allocations = (
            self._distribute_quantity(
                car_component,
                car_phases,
                {phase: car_weight_map.get(phase, 0.0) for phase in car_phases},
            )
            if car_component > 0 and car_phases
            else {phase: 0 for phase in ordered_phases}
        )

        charger_limit_total = int(overall_decision.get("charger_limit", 0) or 0)
        charger_allocations = (
            self._distribute_quantity(
                charger_limit_total,
                car_phases,
                {phase: car_weight_map.get(phase, 0.0) for phase in car_phases},
            )
            if charger_limit_total > 0 and car_phases
            else {phase: 0 for phase in ordered_phases}
        )

        phase_results: Dict[str, Any] = {}
        battery_reason = overall_decision.get("battery_grid_charging_reason")
        car_reason = overall_decision.get("car_grid_charging_reason")

        for phase in ordered_phases:
            grid_from_battery = battery_allocations.get(phase, 0)
            grid_from_car = car_allocations.get(phase, 0)
            grid_setpoint = grid_from_battery + grid_from_car

            has_battery = bool(phase_batteries.get(phase))
            battery_allowed = overall_decision.get("battery_grid_charging", False) and has_battery

            if overall_decision.get("battery_grid_charging", False) and not has_battery:
                phase_battery_reason = "No batteries assigned to this phase"
            else:
                phase_battery_reason = battery_reason

            car_enabled_globally = overall_decision.get("car_grid_charging", False)
            phase_has_car = phase in car_phases
            car_allowed = car_enabled_globally and phase_has_car

            if car_enabled_globally and not phase_has_car:
                phase_car_reason = "No EV feed configured for this phase"
            else:
                phase_car_reason = car_reason

            phase_results[phase] = {
                "grid_setpoint": int(grid_setpoint),
                "grid_components": {
                    "battery": int(grid_from_battery),
                    "car": int(grid_from_car),
                },
                "battery_grid_charging": bool(battery_allowed),
                "battery_grid_charging_reason": phase_battery_reason,
                "car_grid_charging": bool(car_allowed),
                "car_grid_charging_reason": phase_car_reason,
                "charger_limit": int(charger_allocations.get(phase, 0)),
                "battery_entities": [
                    battery["entity_id"] for battery in phase_batteries.get(phase, [])
                ],
                "capacity_share": (
                    phase_capacity_map.get(phase, 0.0) / total_capacity_weight
                    if total_capacity_weight > 0
                    else 0.0
                ),
                "capacity_share_kwh": phase_capacity_map.get(phase, 0.0),
            }

        return phase_results

    def _distribute_quantity(
        self,
        total: int,
        phases: List[str],
        weights: Dict[str, float],
    ) -> Dict[str, int]:
        """Distribute an integer total across phases using weighted rounding."""
        if total <= 0 or not phases:
            return {phase: 0 for phase in phases}

        positive_weights = {phase: max(weights.get(phase, 0.0), 0.0) for phase in phases}
        weight_sum = sum(positive_weights.values())

        if weight_sum <= 0:
            # If no weights are provided, cannot distribute.
            # Fallback to equal distribution is ambiguous; prefer returning zeros.
            return {phase: 0 for phase in phases}

        raw_allocations = {
            phase: (total * positive_weights[phase] / weight_sum) for phase in phases
        }
        allocation = {phase: int(raw_allocations[phase] // 1) for phase in phases}
        remainder = int(total - sum(allocation.values()))

        if remainder > 0:
            fractional_order = sorted(
                phases,
                key=lambda phase: raw_allocations[phase] - allocation[phase],
                reverse=True,
            )
            for phase in fractional_order[:remainder]:
                allocation[phase] += 1

        return allocation

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
            "next_evaluation": dt_util.utcnow() + timedelta(minutes=EVALUATION_INTERVAL_MINUTES),
            "price_analysis": {},
            "power_analysis": {},
            "battery_analysis": {},
            "solar_analysis": {},
            "phase_results": {},
            "phase_mode": PHASE_MODE_SINGLE,
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
            average_soc < max_soc - SOC_SAFETY_MARGIN and
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
            capacity = capacities.get(entity_id, DEFAULT_BATTERY_CAPACITY_KWH)
            
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

        if (
            context.is_low_price_flag
            or (
                context.permissive_mode_active
                and context.effective_low_price
            )
            or (context.previous_charging and context.effective_low_price)
        ):
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

        # Check if permissive mode is active
        permissive_mode_active = bool(data.get("car_permissive_mode_active", False))
        permissive_multiplier = self._settings.car_permissive_threshold_multiplier

        effective_threshold = self._resolve_car_threshold(
            base_threshold, locked_threshold, previous_car_charging,
            permissive_mode_active, permissive_multiplier
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
            permissive_mode_active=permissive_mode_active,
            permissive_multiplier=permissive_multiplier,
        )

    def _resolve_car_threshold(
        self,
        current_threshold: float,
        locked_threshold: Optional[float],
        previous_car_charging: bool,
        permissive_mode_active: bool,
        permissive_multiplier: float,
    ) -> float:
        """Apply hysteresis threshold floor while the car is charging, then permissive multiplier if enabled."""
        # First apply locked threshold floor if charging
        if previous_car_charging and locked_threshold is not None:
            threshold = max(locked_threshold, current_threshold)
            _LOGGER.debug(
                "Car charging active: using threshold floor %.4f€/kWh (locked=%.4f€/kWh, current=%.4f€/kWh)",
                threshold,
                locked_threshold,
                current_threshold,
            )
        else:
            threshold = current_threshold

        # Then apply permissive multiplier if active
        if permissive_mode_active and permissive_multiplier > 1.0:
            permissive_threshold = threshold * permissive_multiplier
            _LOGGER.debug(
                "Permissive mode active: threshold %.4f€/kWh → %.4f€/kWh (+%.0f%%)",
                threshold,
                permissive_threshold,
                (permissive_multiplier - 1) * 100,
            )
            return permissive_threshold

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

    def _append_permissive_mode_to_reason(
        self,
        reason: str,
        context: CarDecisionContext,
    ) -> str:
        """Append permissive mode info to reason string if active."""
        if context.permissive_mode_active and context.permissive_multiplier > 1.0:
            increase_pct = (context.permissive_multiplier - 1.0) * 100
            return f"{reason} [Permissive: +{increase_pct:.0f}%]"
        return reason

    def _format_high_price_reason(self, context: CarDecisionContext) -> str:
        """Create consistent messaging when price exceeds thresholds.

        Note: This is only called from _car_decision_for_high_price(), which means
        the price has exceeded the effective threshold (including any permissive adjustment).
        """
        return f"Price too high ({context.format_price_comparison('>')})"

    def _build_reason_with_solar(
        self,
        base_reason: str,
        context: CarDecisionContext,
        include_solar_inline: bool = False,
    ) -> str:
        """Build charging reason with optional solar allocation details and permissive mode info."""
        if include_solar_inline and context.has_allocated_solar:
            reason = f"{base_reason} with solar ({context.format_solar_watts()})"
        elif context.has_allocated_solar:
            reason = self._append_solar_info_to_reason(base_reason, context)
        else:
            reason = base_reason

        # Always append permissive mode info if active
        return self._append_permissive_mode_to_reason(reason, context)

    def _car_decision_for_very_low_price(
        self,
        context: CarDecisionContext,
        data: Dict[str, Any],
    ) -> CarChargingDecision:
        """Handle very low price cases."""
        price = context.display_price

        if context.previous_charging:
            base_reason = (
                f"Very low price ({price:.3f}€/kWh) - bottom "
                f"{context.very_low_percent}% of daily range (continuing)"
            )
            reason = self._build_reason_with_solar(
                base_reason, context, include_solar_inline=True
            )
            return {
                "car_grid_charging": True,
                "car_grid_charging_reason": reason,
            }
        elif context.has_min_window:
            self._lock_car_charging_threshold(context, data)
            base_reason = (
                f"Very low price ({price:.3f}€/kWh) - bottom "
                f"{context.very_low_percent}% of daily range ({context.min_duration}h+ window available)"
            )
            reason = self._build_reason_with_solar(
                base_reason, context, include_solar_inline=True
            )
            return {
                "car_grid_charging": True,
                "car_grid_charging_reason": reason,
            }
        else:
            base_reason = (
                f"Very low price ({price:.3f}€/kWh) but less than {context.min_duration}h "
                "of low prices ahead - waiting for longer window"
            )
            return {
                "car_grid_charging": False,
                "car_grid_charging_reason": self._append_permissive_mode_to_reason(
                    base_reason, context
                ),
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
            base_reason = (
                f"Low price ({context.format_price_comparison()}) but less than {context.min_duration}h "
                "of low prices ahead - waiting for longer window"
            )
            return {
                "car_grid_charging": False,
                "car_grid_charging_reason": self._append_permissive_mode_to_reason(base_reason, context),
            }

        # At this point, we know has_min_window is False (checked at line 1425)
        # The window check uses the effective threshold (which includes permissive adjustment)
        window_requirement = (
            f"needs ≤ {context.effective_threshold:.3f}€/kWh for ≥ {context.min_duration}h - "
            "current forecast shorter"
        )

        # If permissive mode is active, clarify the threshold breakdown
        if context.permissive_mode_active and context.permissive_multiplier > 1.0:
            window_requirement += f" (base {context.base_threshold:.3f}€/kWh)"

        base_reason = (
            f"Waiting for low-price window before starting "
            f"({context.format_price_comparison()} floor, {window_requirement})"
        )
        return {
            "car_grid_charging": False,
            "car_grid_charging_reason": self._append_permissive_mode_to_reason(base_reason, context),
        }

    def _car_decision_for_high_price(
        self,
        context: CarDecisionContext,
        data: Dict[str, Any],
    ) -> CarChargingDecision:
        """Handle high price cases where charging should pause or fall back to solar."""
        high_price_reason = self._format_high_price_reason(context)

        if context.previous_charging:
            self._unlock_car_charging_threshold(data)
            base_reason = (
                f"{high_price_reason} - stopping car charging"
            )
            return {
                "car_grid_charging": False,
                "car_grid_charging_reason": self._append_permissive_mode_to_reason(base_reason, context),
            }

        if context.has_allocated_solar:
            base_reason = (
                f"{high_price_reason} - "
                f"using allocated solar power only ({context.format_solar_watts()})"
            )
            return {
                "car_grid_charging": True,
                "car_solar_only": True,
                "car_grid_charging_reason": self._append_permissive_mode_to_reason(base_reason, context),
            }

        # Default: price too high, no charging
        return {
            "car_grid_charging": False,
            "car_grid_charging_reason": self._append_permissive_mode_to_reason(high_price_reason, context),
        }

    def _calculate_charger_limit(
        self,
        price_analysis: Dict[str, Any],
        battery_analysis: Dict[str, Any],
        power_allocation: Dict[str, Any],
        data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Calculate the maximum power for the car charger based on decisions."""
        car_grid_charging = data.get("car_grid_charging", False)
        car_solar_only = data.get("car_solar_only", False)
        solar_for_car = power_allocation.get("solar_for_car", 0)

        if car_solar_only:
            return {
                "charger_limit": int(solar_for_car),
                "charger_limit_reason": f"Solar-only car charging ({solar_for_car}W)",
            }

        if not car_grid_charging:
            return {
                "charger_limit": 0,
                "charger_limit_reason": "Car charging from grid is not allowed",
            }

        return {
            "charger_limit": self._settings.max_car_power,
            "charger_limit_reason": f"Car charging from grid allowed (max {self._settings.max_car_power}W)",
        }


    def _calculate_grid_setpoint(
        self,
        price_analysis: Dict[str, Any],
        battery_analysis: Dict[str, Any],
        power_allocation: Dict[str, Any],
        data: Dict[str, Any],
        charger_limit: int,
    ) -> Dict[str, Any]:
        """Calculate grid setpoint based on charging decisions."""
        car_grid_charging = data.get("car_grid_charging", False)
        battery_grid_charging = data.get("battery_grid_charging", False)
        car_solar_only = data.get("car_solar_only", False)

        if car_solar_only:
            return {
                "grid_setpoint": 0,
                "grid_setpoint_reason": "Solar-only car charging, no grid import",
            }

        # Calculate power needed from grid for car and battery
        grid_for_car = 0
        if car_grid_charging:
            solar_for_car = power_allocation.get("solar_for_car", 0)
            car_current_solar = power_allocation.get("car_current_solar_usage", 0)
            car_power_total = min(charger_limit, self._settings.max_car_power)
            grid_for_car = max(0, car_power_total - solar_for_car - car_current_solar)

        grid_for_battery = 0
        if battery_grid_charging and battery_analysis.get("average_soc") is not None:
            soc_room = battery_analysis["max_soc_threshold"] - battery_analysis["average_soc"]
            soc_ratio = min(1.0, max(0.0, soc_room / 20.0))  # Scale up as battery gets emptier
            grid_for_battery = int(self._settings.max_battery_power * soc_ratio)

        total_grid_draw = grid_for_car + grid_for_battery

        # Enforce monthly peak limit
        monthly_peak = data.get("monthly_grid_peak")
        if monthly_peak is not None and monthly_peak > 0:
            peak_limit = monthly_peak * (1 - DEFAULT_MONTHLY_PEAK_SAFETY_MARGIN)
            if total_grid_draw > peak_limit:
                scale_factor = peak_limit / total_grid_draw if total_grid_draw > 0 else 0
                grid_for_car = int(grid_for_car * scale_factor)
                grid_for_battery = int(grid_for_battery * scale_factor)
                total_grid_draw = peak_limit

        # Enforce base grid setpoint (e.g., house load) and absolute max
        grid_setpoint = min(
            max(total_grid_draw, self._settings.base_grid_setpoint if total_grid_draw > 0 else 0),
            self._settings.max_grid_power,
        )

        reason = (
            f"Grid setpoint: car {grid_for_car}W, battery {grid_for_battery}W "
            f"(total {total_grid_draw}W), base {self._settings.base_grid_setpoint}W, "
            f"limit {self._settings.max_grid_power}W -> {grid_setpoint}W"
        )

        return {"grid_setpoint": int(grid_setpoint), "grid_setpoint_reason": reason, "grid_components": {"car": grid_for_car, "battery": grid_for_battery}}
