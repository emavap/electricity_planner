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
        return 5  # After DynamicPriceStrategy


class SolarAwareChargingStrategy(ChargingStrategy):
    """Solar-aware charging that waits for solar production when forecasted."""

    def should_charge(self, context: Dict[str, Any]) -> Tuple[bool, str]:
        """Check if we should wait for solar production instead of grid charging."""
        price = context.get("price_analysis", {})
        battery = context.get("battery_analysis", {})
        power = context.get("power_analysis", {})
        config = context.get("config", {})
        time_ctx = context.get("time_context", {})

        if not price.get("is_low_price", False):
            return False, ""

        average_soc = battery.get("average_soc", 0)
        is_solar_peak = time_ctx.get("is_solar_peak", False)
        has_significant_solar = power.get("significant_solar_surplus", False)

        # During solar peak hours with significant solar - wait for solar unless emergency
        solar_peak_emergency = config.get("solar_peak_emergency_soc", 25)

        if is_solar_peak and has_significant_solar:
            # Emergency override if SOC too low
            if average_soc < solar_peak_emergency:
                return True, (f"Emergency override during solar peak - SOC {average_soc:.0f}% < "
                            f"{solar_peak_emergency}% too low to wait for solar")

            # Wait for solar if SOC is sufficient
            solar_surplus = power.get("solar_surplus", 0)
            return False, (f"Solar peak hours - SOC {average_soc:.0f}% sufficient, awaiting solar production "
                         f"(surplus: {solar_surplus}W)")

        return False, ""

    def get_priority(self) -> int:
        return 6  # After PredictiveChargingStrategy


class SOCBasedChargingStrategy(ChargingStrategy):
    """Charging based on SOC levels and solar forecast."""

    def should_charge(self, context: Dict[str, Any]) -> Tuple[bool, str]:
        """Check SOC-based charging conditions."""
        price = context.get("price_analysis", {})
        battery = context.get("battery_analysis", {})
        power = context.get("power_analysis", {})
        config = context.get("config", {})

        if not price.get("is_low_price", False):
            return False, ""

        average_soc = battery.get("average_soc", 0)
        has_significant_solar = power.get("significant_solar_surplus", False)
        solar_surplus = power.get("solar_surplus", 0)

        # Low SOC + no solar → charge
        if average_soc < DEFAULT_ALGORITHM_THRESHOLDS.low_soc_threshold and not has_significant_solar:
            return True, (f"Low SOC {average_soc:.0f}% + no significant solar "
                         f"(surplus: {solar_surplus}W) - charge while price low")

        # Medium SOC + significant solar → skip and wait for solar
        if (average_soc >= DEFAULT_ALGORITHM_THRESHOLDS.low_soc_threshold and
            average_soc <= DEFAULT_ALGORITHM_THRESHOLDS.high_soc_threshold and
            has_significant_solar):
            return False, (f"SOC {average_soc:.0f}% sufficient + significant solar "
                          f"(surplus: {solar_surplus}W) - waiting for solar instead of grid")

        # Medium SOC → charge at low price
        if average_soc < DEFAULT_ALGORITHM_THRESHOLDS.medium_soc_threshold:
            return True, (f"Medium SOC {average_soc:.0f}% < {DEFAULT_ALGORITHM_THRESHOLDS.medium_soc_threshold}% - "
                         f"charge at low price")

        return False, ""

    def get_priority(self) -> int:
        return 7  # Last - safety net


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

        # Get base confidence from config (default 60%) and clamp to sensible range
        config_confidence = config.get("dynamic_threshold_confidence", 60)
        try:
            config_confidence = float(config_confidence) / 100.0
        except (TypeError, ValueError):
            config_confidence = 0.6
        config_confidence = min(max(config_confidence, 0.3), 0.9)

        # Prepare SOC/solar adjustments before running the analyzer so reasons align
        average_soc = battery.get("average_soc", 50)
        power = context.get("power_analysis", {})
        has_significant_solar = power.get("significant_solar_surplus", False)
        solar_surplus = power.get("solar_surplus", 0)

        confidence_threshold = config_confidence

        # Adjust based on SOC (make it easier to charge when battery is low)
        if average_soc < 40:
            confidence_threshold = max(0.3, confidence_threshold - 0.1)
        elif average_soc >= 70:
            confidence_threshold = min(0.9, confidence_threshold + 0.1)

        # Adjust based on actual solar surplus
        # Significant surplus → need +10% more confidence (be picky, wait for solar)
        # No surplus → need -10% less confidence (less picky, no solar available)
        if has_significant_solar:
            confidence_threshold = min(0.9, confidence_threshold + 0.1)
            solar_context = f"significant solar surplus ({solar_surplus}W) - waiting for better prices"
        elif solar_surplus > 0:
            solar_context = f"minor solar surplus ({solar_surplus}W)"
        else:
            confidence_threshold = max(0.3, confidence_threshold - 0.1)
            solar_context = "no solar surplus - accepting okay prices"

        # Initialize analyzer if needed, or update threshold if config changed
        if self.dynamic_analyzer is None:
            self.dynamic_analyzer = DynamicThresholdAnalyzer(threshold, confidence_threshold)
        else:
            # Update threshold and confidence in case user changed config at runtime
            self.dynamic_analyzer.max_threshold = threshold
            self.dynamic_analyzer.base_confidence = confidence_threshold

        # Get price data (handle None during Nord Pool refresh)
        # IMPORTANT: Use explicit None checks since 0.0 and negative prices are valid
        highest_price = price.get("highest_price")
        if highest_price is None:
            highest_price = current_price

        lowest_price = price.get("lowest_price")
        if lowest_price is None:
            lowest_price = current_price

        next_price = price.get("next_price")  # Can be None

        # Get dynamic analysis (only uses real Nord Pool data)
        analysis = self.dynamic_analyzer.analyze_price_window(
            current_price=current_price,
            highest_today=highest_price,
            lowest_today=lowest_price,
            next_price=next_price
        )

        # Simple decision: does price analysis meet confidence requirement?
        if analysis["confidence"] >= confidence_threshold:
            reason = f"{analysis['reason']} (SOC: {average_soc:.0f}%, {solar_context})"
            return True, reason

        return False, (
            f"{analysis['reason']} "
            f"(Need {confidence_threshold:.0%} confidence, have {analysis['confidence']:.0%}; "
            f"{solar_context})"
        )
    
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
            self.dynamic_price_strategy = DynamicPriceStrategy()
            self.strategies.append(self.dynamic_price_strategy)
        else:
            self.dynamic_price_strategy = None

        # Add remaining strategies
        self.strategies.extend([
            PredictiveChargingStrategy(),
            SolarAwareChargingStrategy(),
            SOCBasedChargingStrategy(),
        ])

        # Sort by priority
        self.strategies.sort(key=lambda s: s.get_priority())
    
    def evaluate(self, context: Dict[str, Any]) -> Tuple[bool, str]:
        """Evaluate all strategies and return decision.

        Strategies are evaluated in priority order:
        - If a strategy returns True (charge), evaluation stops immediately
        - If a strategy returns False with a reason, the reason is saved but evaluation continues
        - This allows lower-priority safety nets (SOCBased) to override advisory decisions

        Exception: EmergencyChargingStrategy can override price threshold check.
        """
        # Hard stop: Price too high (unless emergency overrides it)
        price = context.get("price_analysis", {})
        current_price = price.get("current_price")
        threshold = price.get("price_threshold", 0.15)

        if current_price is not None and current_price > threshold:
            # Check if emergency charging applies
            battery = context.get("battery_analysis", {})
            config = context.get("config", {})
            average_soc = battery.get("average_soc", 100)
            emergency_threshold = config.get("emergency_soc_threshold", 15)

            if average_soc < emergency_threshold:
                return True, (f"Emergency charge - SOC {average_soc:.0f}% < {emergency_threshold}% threshold, "
                            f"charging regardless of price ({current_price:.3f}€/kWh)")
            else:
                return False, f"Price {current_price:.3f}€/kWh exceeds maximum threshold {threshold:.3f}€/kWh"

        last_reason = ""

        for strategy in self.strategies:
            should_charge, reason = strategy.should_charge(context)

            if should_charge:
                _LOGGER.debug("Strategy %s decided to charge: %s",
                            strategy.__class__.__name__, reason)
                return True, reason
            elif reason:  # Strategy made a decision not to charge, but continue checking
                _LOGGER.debug("Strategy %s suggests not charging: %s (continuing evaluation)",
                            strategy.__class__.__name__, reason)
                last_reason = reason

        # If we got here, no strategy said to charge
        # Use the last reason provided, or generate default
        if last_reason:
            return False, last_reason

        # Default decision if no strategy provided a reason
        battery = context.get("battery_analysis", {})
        position = price.get("price_position")
        average_soc = battery.get("average_soc", 0)

        price_fragment = (
            f"{current_price:.3f}€/kWh" if current_price is not None else "unknown price"
        )
        position_fragment = (
            f"{position:.0%} of daily range" if position is not None else "unknown price position"
        )

        return False, (f"Price not favorable ({price_fragment}, "
                      f"{position_fragment}) for SOC {average_soc:.0f}%")

    def get_dynamic_threshold(self, context: Dict[str, Any]) -> Optional[float]:
        """Get the current dynamic threshold if dynamic pricing is enabled.

        Returns None if dynamic threshold is not active or cannot be calculated.
        """
        if self.dynamic_price_strategy is None:
            return None

        if self.dynamic_price_strategy.dynamic_analyzer is None:
            return None

        # Get price data from context
        price = context.get("price_analysis", {})
        current_price = price.get("current_price")
        highest_price = price.get("highest_price")
        lowest_price = price.get("lowest_price")
        next_price = price.get("next_price")

        # Need at least current and highest/lowest to calculate
        if current_price is None or highest_price is None or lowest_price is None:
            return None

        # Get the analysis which includes dynamic_threshold
        analysis = self.dynamic_price_strategy.dynamic_analyzer.analyze_price_window(
            current_price=current_price,
            highest_today=highest_price,
            lowest_today=lowest_price,
            next_price=next_price
        )

        return analysis.get("dynamic_threshold")
