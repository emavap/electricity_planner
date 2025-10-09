"""Charging strategies for decision making."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Tuple
import logging

from .defaults import DEFAULT_ALGORITHM_THRESHOLDS
from .dynamic_threshold import DynamicThresholdAnalyzer

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
        config = context.get("config", {})

        if not price.get("very_low_price", False):
            return False, ""

        # Simple logic: Very low price → charge (unless battery is full)
        max_soc = battery.get("max_soc_threshold", 90)
        average_soc = battery.get("average_soc", 0)

        if average_soc >= max_soc:
            return False, ""  # Let other strategies handle full battery

        current_price = price.get("current_price", 0)
        very_low_threshold = config.get("very_low_price_threshold", 30)
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

        if not price.get("is_low_price", False):
            return False, ""

        if not price.get("significant_price_drop", False):
            return False, ""

        average_soc = battery.get("average_soc", 0)
        predictive_min = config.get("predictive_charging_min_soc", 30)

        # Too low to wait - let other strategies handle it
        if average_soc <= predictive_min:
            return False, ""

        # Wait for the price drop
        next_price = price.get("next_price", 0)
        return False, (f"SOC {average_soc:.0f}% sufficient - waiting for significant price drop "
                      f"next hour ({next_price:.3f}€/kWh)")

    def get_priority(self) -> int:
        return 4


class SolarAwareChargingStrategy(ChargingStrategy):
    """Solar-aware charging that waits for solar production when forecasted."""

    def should_charge(self, context: Dict[str, Any]) -> Tuple[bool, str]:
        """Check if we should wait for solar production instead of grid charging."""
        price = context.get("price_analysis", {})
        battery = context.get("battery_analysis", {})
        solar = context.get("solar_forecast", {})
        config = context.get("config", {})
        time_ctx = context.get("time_context", {})

        if not price.get("is_low_price", False):
            return False, ""

        average_soc = battery.get("average_soc", 0)
        is_solar_peak = time_ctx.get("is_solar_peak", False)
        solar_factor = solar.get("solar_production_factor", 0.5)

        # During solar peak hours with good forecast - wait for solar unless emergency
        solar_peak_emergency = config.get("solar_peak_emergency_soc", 25)

        if is_solar_peak and solar_factor > DEFAULT_ALGORITHM_THRESHOLDS.moderate_solar_threshold:
            # Emergency override if SOC too low
            if average_soc < solar_peak_emergency:
                return True, (f"Emergency override during solar peak - SOC {average_soc:.0f}% < "
                            f"{solar_peak_emergency}% too low to wait for solar")

            # Wait for solar if SOC is sufficient
            return False, (f"Solar peak hours - SOC {average_soc:.0f}% sufficient, awaiting solar production "
                         f"(forecast: {solar_factor:.0%})")

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

        # Low SOC + poor solar forecast → charge
        if average_soc < DEFAULT_ALGORITHM_THRESHOLDS.low_soc_threshold and solar_factor < poor_threshold:
            return True, (f"Low SOC {average_soc:.0f}% + {expected_solar} solar forecast "
                         f"({solar_factor:.0%} < {poor_threshold:.0%}) - charge while price low")

        # Medium SOC + excellent solar forecast → skip and wait for solar
        if (average_soc >= DEFAULT_ALGORITHM_THRESHOLDS.low_soc_threshold and
            average_soc <= DEFAULT_ALGORITHM_THRESHOLDS.high_soc_threshold and
            solar_factor > excellent_threshold):
            return False, (f"SOC {average_soc:.0f}% sufficient + {expected_solar} solar forecast "
                          f"({solar_factor:.0%} > {excellent_threshold:.0%})")

        # Medium SOC → charge at low price
        if average_soc < DEFAULT_ALGORITHM_THRESHOLDS.medium_soc_threshold:
            return True, (f"Medium SOC {average_soc:.0f}% < {DEFAULT_ALGORITHM_THRESHOLDS.medium_soc_threshold}% + "
                         f"{expected_solar} conditions - charge at low price")

        return False, ""

    def get_priority(self) -> int:
        return 6


class DynamicPriceStrategy(ChargingStrategy):
    """Dynamic price-based charging with intelligent threshold logic."""
    
    def __init__(self):
        """Initialize dynamic price strategy."""
        self.dynamic_analyzer = None
    
    def should_charge(self, context: Dict[str, Any]) -> Tuple[bool, str]:
        """Check if charging should occur based on dynamic price analysis."""
        price = context.get("price_analysis", {})
        battery = context.get("battery_analysis", {})
        config = context.get("config", {})
        
        current_price = price.get("current_price")
        if current_price is None:
            return False, ""
        
        threshold = price.get("price_threshold", 0.15)
        
        # Initialize analyzer if needed
        if self.dynamic_analyzer is None:
            self.dynamic_analyzer = DynamicThresholdAnalyzer(threshold)
        
        # Never charge above absolute threshold
        if current_price > threshold:
            return False, f"Price {current_price:.3f}€/kWh exceeds maximum threshold {threshold:.3f}€/kWh"
        
        # Get dynamic analysis
        analysis = self.dynamic_analyzer.analyze_price_window(
            current_price=current_price,
            highest_today=price.get("highest_price", current_price),
            lowest_today=price.get("lowest_price", current_price),
            next_price=price.get("next_price"),
            next_6h_prices=context.get("next_6h_prices", [])
        )
        
        # Check confidence threshold based on battery SOC
        average_soc = battery.get("average_soc", 50)
        
        # Lower confidence requirement when battery is low
        if average_soc < 30:
            confidence_threshold = 0.4  # Charge more readily when low
        elif average_soc < 50:
            confidence_threshold = 0.5
        elif average_soc < 70:
            confidence_threshold = 0.6
        else:
            confidence_threshold = 0.7  # Be very selective when nearly full
        
        if analysis["confidence"] >= confidence_threshold:
            # Add SOC context to reason
            if average_soc < 30:
                reason = f"{analysis['reason']} (Low SOC {average_soc:.0f}% - less selective)"
            elif average_soc > 70:
                reason = f"{analysis['reason']} (High SOC {average_soc:.0f}% - more selective)"
            else:
                reason = analysis["reason"]
            
            # Store analysis details in context for diagnostics
            if "dynamic_price_analysis" not in context:
                context["dynamic_price_analysis"] = analysis
            
            return True, reason
        
        return False, analysis["reason"]
    
    def get_priority(self) -> int:
        return 4  # After emergency, solar, and very low price


class StrategyManager:
    """Manage and execute charging strategies."""
    
    def __init__(self, use_dynamic_threshold: bool = True):
        """Initialize strategy manager."""
        # Always include core strategies
        self.strategies = [
            EmergencyChargingStrategy(),
            SolarPriorityStrategy(),
            VeryLowPriceStrategy(),
        ]
        
        # Conditionally add dynamic or traditional strategies
        if use_dynamic_threshold:
            self.strategies.append(DynamicPriceStrategy())
        
        # Add remaining strategies
        self.strategies.extend([
            PredictiveChargingStrategy(),
            SolarAwareChargingStrategy(),
            SOCBasedChargingStrategy(),
        ])
        
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
