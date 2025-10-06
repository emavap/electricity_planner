"""Charging strategies for decision making."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Tuple
import logging

from .defaults import (
    DEFAULT_ALGORITHM_THRESHOLDS,
    DEFAULT_TIME_SCHEDULE,
    DEFAULT_POWER_ESTIMATES,
)

_LOGGER = logging.getLogger(__name__)


class ChargingStrategy(ABC):
    """Abstract base class for charging strategies."""
    
    @abstractmethod
    def should_charge(self, context: Dict[str, Any]) -> Tuple[bool, str]:
        """Determine if charging should occur."""
        pass
    
    @abstractmethod
    def get_priority(self) -> int:
        """Get strategy priority (lower = higher priority)."""
        pass


class EmergencyChargingStrategy(ChargingStrategy):
    """Emergency charging when SOC is critically low."""
    
    def should_charge(self, context: Dict[str, Any]) -> Tuple[bool, str]:
        """Check if emergency charging is needed."""
        battery = context.get("battery_analysis", {})
        config = context.get("config", {})
        price = context.get("price_analysis", {})
        
        average_soc = battery.get("average_soc", 100)
        emergency_threshold = config.get("emergency_soc_threshold", 15)
        current_price = price.get("current_price", 0)
        
        if average_soc < emergency_threshold:
            return True, (f"Emergency charge - SOC {average_soc:.0f}% < {emergency_threshold}% threshold, "
                         f"charging regardless of price ({current_price:.3f}€/kWh)")
        
        return False, ""
    
    def get_priority(self) -> int:
        return 1  # Highest priority


class SolarPriorityStrategy(ChargingStrategy):
    """Prefer solar charging over grid."""
    
    def should_charge(self, context: Dict[str, Any]) -> Tuple[bool, str]:
        """Check if solar should be used instead of grid."""
        allocation = context.get("power_allocation", {})
        battery = context.get("battery_analysis", {})
        
        allocated_solar = allocation.get("solar_for_batteries", 0)
        remaining_solar = allocation.get("remaining_solar", 0)
        average_soc = battery.get("average_soc", 0)
        max_soc = battery.get("max_soc_threshold", 90)
        
        if allocated_solar > 0:
            return False, f"Using allocated solar power ({allocated_solar}W) for batteries instead of grid"
        
        # Prevent solar waste when batteries nearly full
        if remaining_solar > 0 and average_soc >= max_soc - DEFAULT_ALGORITHM_THRESHOLDS.soc_buffer:
            return False, (f"Battery {average_soc:.0f}% nearly full with {remaining_solar}W "
                          f"solar surplus - preventing solar waste")
        
        return False, ""
    
    def get_priority(self) -> int:
        return 2


class VeryLowPriceStrategy(ChargingStrategy):
    """Charge when price is in bottom percentage of daily range."""
    
    def should_charge(self, context: Dict[str, Any]) -> Tuple[bool, str]:
        """Check if price is very low."""
        price = context.get("price_analysis", {})
        battery = context.get("battery_analysis", {})
        solar = context.get("solar_forecast", {})
        config = context.get("config", {})
        allocation = context.get("power_allocation", {})
        
        if not price.get("very_low_price", False):
            return False, ""
        
        current_price = price.get("current_price", 0)
        very_low_threshold = config.get("very_low_price_threshold", 30)
        grid_charging_limit = config.get("grid_battery_charging_limit_soc", 80)
        average_soc = battery.get("average_soc", 0)
        remaining_solar = allocation.get("remaining_solar", 0)
        
        # Check if we should skip charging despite very low price
        if average_soc >= grid_charging_limit:
            # Check current solar
            if remaining_solar > 0:
                return False, (f"Battery above grid charging limit ({average_soc:.0f}% ≥ {grid_charging_limit}%) "
                             f"with {remaining_solar}W solar surplus - avoid grid charging despite very low price")
            
            # Check solar forecast
            forecast_remaining = solar.get("forecast_remaining_today_kwh")
            if forecast_remaining is not None:
                estimated_surplus = forecast_remaining * DEFAULT_POWER_ESTIMATES.house_consumption_factor
                max_soc = battery.get("max_soc_threshold", 90)
                target_soc = min(max_soc, DEFAULT_ALGORITHM_THRESHOLDS.max_target_soc)
                soc_deficit = max(0, target_soc - average_soc)
                kwh_needed = soc_deficit * DEFAULT_POWER_ESTIMATES.kwh_per_soc_percent
                
                if estimated_surplus >= kwh_needed:
                    return False, (f"Battery {average_soc:.0f}% with {forecast_remaining:.1f}kWh solar remaining "
                                 f"(≈{estimated_surplus:.1f}kWh surplus) - sufficient to reach {target_soc}% - "
                                 f"skip grid charging")
            
            # Check if tomorrow has excellent solar
            solar_factor = solar.get("solar_production_factor", 0.5)
            if solar_factor > DEFAULT_ALGORITHM_THRESHOLDS.excellent_solar_threshold:
                return False, (f"Battery above grid charging limit ({average_soc:.0f}% ≥ {grid_charging_limit}%) "
                             f"+ excellent solar forecast ({solar_factor:.0%}) - skip charging despite very low price")
        
        return True, f"Very low price ({current_price:.3f}€/kWh) - bottom {very_low_threshold}% of daily range"
    
    def get_priority(self) -> int:
        return 3


class PredictiveChargingStrategy(ChargingStrategy):
    """Skip charging if significant price drop is expected."""
    
    def should_charge(self, context: Dict[str, Any]) -> Tuple[bool, str]:
        """Check if we should wait for better prices."""
        price = context.get("price_analysis", {})
        battery = context.get("battery_analysis", {})
        config = context.get("config", {})
        time_ctx = context.get("time_context", {})
        
        if not price.get("is_low_price", False):
            return False, ""
        
        if not price.get("significant_price_drop", False):
            return False, ""
        
        average_soc = battery.get("average_soc", 0)
        predictive_min = config.get("predictive_charging_min_soc", 30)
        
        if average_soc <= predictive_min:
            return False, ""  # Too low to wait
        
        # Check for emergency overrides
        emergency_override = config.get("emergency_soc_override", 25)
        winter_night_override = config.get("winter_night_soc_override", 40)
        is_night = time_ctx.get("is_night", False)
        is_winter = time_ctx.get("winter_season", False)
        
        if average_soc < emergency_override:
            return True, f"Emergency override - SOC {average_soc:.0f}% too low to wait for price drop"
        
        if is_night and is_winter and average_soc < winter_night_override:
            return True, (f"Emergency override - SOC {average_soc:.0f}% too low to wait for price drop "
                         f"(winter night: True)")
        
        next_price = price.get("next_price", 0)
        return False, (f"SOC {average_soc:.0f}% sufficient - waiting for significant price drop "
                      f"next hour ({next_price:.3f}€/kWh)")
    
    def get_priority(self) -> int:
        return 4


class TimeBasedChargingStrategy(ChargingStrategy):
    """Time-aware charging based on time of day and season."""
    
    def should_charge(self, context: Dict[str, Any]) -> Tuple[bool, str]:
        """Check time-based charging conditions."""
        price = context.get("price_analysis", {})
        battery = context.get("battery_analysis", {})
        solar = context.get("solar_forecast", {})
        config = context.get("config", {})
        time_ctx = context.get("time_context", {})
        
        if not price.get("is_low_price", False):
            return False, ""
        
        average_soc = battery.get("average_soc", 0)
        current_price = price.get("current_price", 0)
        is_night = time_ctx.get("is_night", False)
        is_solar_peak = time_ctx.get("is_solar_peak", False)
        is_winter = time_ctx.get("winter_season", False)
        solar_factor = solar.get("solar_production_factor", 0.5)
        
        # Solar peak hours - be conservative
        if is_solar_peak and average_soc > DEFAULT_ALGORITHM_THRESHOLDS.critical_low_soc:
            if solar_factor > DEFAULT_ALGORITHM_THRESHOLDS.moderate_solar_threshold:
                solar_peak_emergency = config.get("solar_peak_emergency_soc", 25)
                if average_soc < solar_peak_emergency:
                    return True, (f"Emergency override during solar peak - SOC {average_soc:.0f}% < "
                                f"{solar_peak_emergency}% too low to wait for solar")
                
                return False, (f"Solar peak hours - SOC {average_soc:.0f}% sufficient, awaiting solar production "
                             f"(forecast: {solar_factor:.0%})")
        
        # Night + Winter
        if is_night and is_winter and average_soc < DEFAULT_ALGORITHM_THRESHOLDS.high_soc_threshold:
            return True, (f"Night + Winter charging - SOC {average_soc:.0f}% at low price "
                         f"({current_price:.3f}€/kWh) during off-peak winter hours")
        
        # Night only
        if is_night and average_soc < DEFAULT_ALGORITHM_THRESHOLDS.high_soc_threshold:
            return True, (f"Night charging - SOC {average_soc:.0f}% at low price "
                         f"({current_price:.3f}€/kWh) during off-peak hours")
        
        # Winter only (daytime)
        if is_winter and average_soc < DEFAULT_ALGORITHM_THRESHOLDS.medium_soc_threshold:
            return True, (f"Winter season - SOC {average_soc:.0f}% < "
                         f"{DEFAULT_ALGORITHM_THRESHOLDS.medium_soc_threshold}% with low price "
                         f"({current_price:.3f}€/kWh)")
        
        return False, ""
    
    def get_priority(self) -> int:
        return 5


class SOCBasedChargingStrategy(ChargingStrategy):
    """Charging based on SOC levels and solar forecast."""
    
    def should_charge(self, context: Dict[str, Any]) -> Tuple[bool, str]:
        """Check SOC-based charging conditions."""
        price = context.get("price_analysis", {})
        battery = context.get("battery_analysis", {})
        solar = context.get("solar_forecast", {})
        config = context.get("config", {})
        
        if not price.get("is_low_price", False):
            return False, ""
        
        average_soc = battery.get("average_soc", 0)
        solar_factor = solar.get("solar_production_factor", 0.5)
        poor_threshold = config.get("poor_solar_forecast_threshold", 40) / 100
        excellent_threshold = config.get("excellent_solar_forecast_threshold", 80) / 100
        expected_solar = solar.get("expected_solar_production", "moderate")
        
        # Critical low - always charge
        if average_soc < DEFAULT_ALGORITHM_THRESHOLDS.critical_low_soc:
            return True, (f"Critical SOC {average_soc:.0f}% < {DEFAULT_ALGORITHM_THRESHOLDS.critical_low_soc}% - "
                         f"charge despite {expected_solar} solar forecast")
        
        # Low + poor forecast
        if average_soc < DEFAULT_ALGORITHM_THRESHOLDS.low_soc_threshold and solar_factor < poor_threshold:
            return True, (f"Low SOC {average_soc:.0f}% + {expected_solar} solar forecast "
                         f"({solar_factor:.0%} < {poor_threshold:.0%}) - charge while price low")
        
        # Medium + excellent forecast - skip
        if (DEFAULT_ALGORITHM_THRESHOLDS.critical_low_soc <= average_soc <= 
            DEFAULT_ALGORITHM_THRESHOLDS.high_soc_threshold and 
            solar_factor > excellent_threshold):
            return False, (f"SOC {average_soc:.0f}% sufficient + {expected_solar} solar forecast "
                          f"({solar_factor:.0%} > {excellent_threshold:.0%})")
        
        # Medium SOC - charge
        if average_soc < DEFAULT_ALGORITHM_THRESHOLDS.medium_soc_threshold:
            return True, (f"Medium SOC {average_soc:.0f}% < {DEFAULT_ALGORITHM_THRESHOLDS.medium_soc_threshold}% + "
                         f"{expected_solar} conditions - charge at low price")
        
        return False, ""
    
    def get_priority(self) -> int:
        return 6


class StrategyManager:
    """Manage and execute charging strategies."""
    
    def __init__(self):
        """Initialize strategy manager."""
        self.strategies = [
            EmergencyChargingStrategy(),
            SolarPriorityStrategy(),
            VeryLowPriceStrategy(),
            PredictiveChargingStrategy(),
            TimeBasedChargingStrategy(),
            SOCBasedChargingStrategy(),
        ]
        # Sort by priority
        self.strategies.sort(key=lambda s: s.get_priority())
    
    def evaluate(self, context: Dict[str, Any]) -> Tuple[bool, str]:
        """Evaluate all strategies and return decision."""
        for strategy in self.strategies:
            should_charge, reason = strategy.should_charge(context)
            
            if should_charge:
                _LOGGER.debug("Strategy %s decided to charge: %s",
                            strategy.__class__.__name__, reason)
                return True, reason
            elif reason:  # Strategy made a decision not to charge
                _LOGGER.debug("Strategy %s decided not to charge: %s",
                            strategy.__class__.__name__, reason)
                return False, reason
        
        # Default decision if no strategy applies
        price = context.get("price_analysis", {})
        battery = context.get("battery_analysis", {})
        current_price = price.get("current_price", 0)
        position = price.get("price_position", 0.5)
        average_soc = battery.get("average_soc", 0)
        
        return False, (f"Price not favorable ({current_price:.3f}€/kWh, "
                      f"{position:.0%} of daily range) for SOC {average_soc:.0f}%")
