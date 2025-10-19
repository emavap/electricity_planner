"""Charging strategies for decision making."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple
import logging

from .const import (
    DEFAULT_DYNAMIC_THRESHOLD_CONFIDENCE,
    DEFAULT_EMERGENCY_SOC,
    DEFAULT_PREDICTIVE_CHARGING_MIN_SOC,
    DEFAULT_VERY_LOW_PRICE_THRESHOLD,
    SOC_BUFFER_FOR_SOLAR_WASTE,
    LOW_SOC_THRESHOLD,
    HIGH_SOC_THRESHOLD,
    MEDIUM_SOC_THRESHOLD,
    CONFIDENCE_BASE,
    CONFIDENCE_RANGE_MIN,
    CONFIDENCE_RANGE_MAX,
    CONFIDENCE_LOW_SOC_ADJUSTMENT,
    CONFIDENCE_HIGH_SOC_ADJUSTMENT,
    CONFIDENCE_SOLAR_ADJUSTMENT,
)
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
        config = context.get("config", {}) or {}
        settings = context.get("settings")
        price = context.get("price_analysis", {})

        average_soc = battery.get("average_soc", 100)
        emergency_threshold = (
            getattr(settings, "emergency_soc_threshold", None)
            if settings is not None
            else None
        )
        if emergency_threshold is None:
            emergency_threshold = config.get("emergency_soc_threshold", DEFAULT_EMERGENCY_SOC)
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
        average_soc = battery.get("average_soc")
        max_soc = battery.get("max_soc_threshold", 90)

        # If battery data unavailable, cannot determine solar priority
        if average_soc is None:
            return False, ""
        
        if allocated_solar > 0:
            return False, f"Using allocated solar power ({allocated_solar}W) for batteries instead of grid"
        
        # Prevent solar waste when batteries nearly full
        if remaining_solar > 0 and average_soc >= max_soc - SOC_BUFFER_FOR_SOLAR_WASTE:
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
        config = context.get("config", {}) or {}
        settings = context.get("settings")

        if not price.get("very_low_price", False):
            return False, ""

        # Simple logic: Very low price → charge (unless battery is full)
        max_soc = battery.get("max_soc_threshold", 90)
        average_soc = battery.get("average_soc")

        # If battery data unavailable, default to charging (price is very low!)
        very_low_threshold = (
            getattr(settings, "very_low_price_threshold_pct", None)
            if settings is not None
            else None
        )
        if very_low_threshold is None:
            very_low_threshold = config.get("very_low_price_threshold", DEFAULT_VERY_LOW_PRICE_THRESHOLD)

        if average_soc is None:
            current_price = price.get("current_price", 0)
            return True, f"Very low price ({current_price:.3f}€/kWh) - bottom {very_low_threshold}% (battery data unavailable, charging anyway)"

        if average_soc >= max_soc:
            return False, ""  # Let other strategies handle full battery

        current_price = price.get("current_price", 0)
        return True, f"Very low price ({current_price:.3f}€/kWh) - bottom {very_low_threshold}% of daily range"

    def get_priority(self) -> int:
        return 3


class PredictiveChargingStrategy(ChargingStrategy):
    """Skip charging if significant price drop is expected."""

    def should_charge(self, context: Dict[str, Any]) -> Tuple[bool, str]:
        """Check if we should wait for better prices."""
        price = context.get("price_analysis", {})
        battery = context.get("battery_analysis", {})
        config = context.get("config", {}) or {}
        settings = context.get("settings")

        if not price.get("is_low_price", False):
            return False, ""

        if not price.get("significant_price_drop", False):
            return False, ""

        average_soc = battery.get("average_soc")
        predictive_min = (
            getattr(settings, "predictive_min_soc", None)
            if settings is not None
            else None
        )
        if predictive_min is None:
            predictive_min = config.get("predictive_charging_min_soc", DEFAULT_PREDICTIVE_CHARGING_MIN_SOC)

        # If battery data unavailable, cannot predict - let other strategies decide
        if average_soc is None:
            return False, ""

        # Too low to wait - let other strategies handle it
        if average_soc <= predictive_min:
            return False, ""

        # Wait for the price drop
        next_price = price.get("next_price", 0)
        return False, (f"SOC {average_soc:.0f}% sufficient - waiting for significant price drop "
                      f"next hour ({next_price:.3f}€/kWh)")

    def get_priority(self) -> int:
        return 5  # After DynamicPriceStrategy


class SOCBasedChargingStrategy(ChargingStrategy):
    """Charging based on SOC levels and live solar availability."""

    def should_charge(self, context: Dict[str, Any]) -> Tuple[bool, str]:
        """Check SOC-based charging conditions."""
        price = context.get("price_analysis", {})
        battery = context.get("battery_analysis", {})
        power = context.get("power_analysis", {})
        config = context.get("config", {})

        if not price.get("is_low_price", False):
            return False, ""

        average_soc = battery.get("average_soc")
        has_significant_solar = power.get("significant_solar_surplus", False)
        solar_surplus = power.get("solar_surplus", 0)

        # If battery data unavailable, cannot make SOC-based decision
        if average_soc is None:
            return False, ""

        # Low SOC + no solar → charge
        if average_soc < LOW_SOC_THRESHOLD and not has_significant_solar:
            return True, (f"Low SOC {average_soc:.0f}% + no significant solar "
                         f"(surplus: {solar_surplus}W) - charge while price low")

        # Medium SOC + significant solar → skip and wait for solar
        if (average_soc >= LOW_SOC_THRESHOLD and
            average_soc <= HIGH_SOC_THRESHOLD and
            has_significant_solar):
            return False, (f"SOC {average_soc:.0f}% sufficient + significant solar "
                          f"(surplus: {solar_surplus}W) - waiting for solar instead of grid")

        # Medium SOC → charge at low price
        if average_soc < MEDIUM_SOC_THRESHOLD:
            return True, (f"Medium SOC {average_soc:.0f}% < {MEDIUM_SOC_THRESHOLD}% - "
                         f"charge at low price")

        return False, ""

    def get_priority(self) -> int:
        return 6  # Last - safety net


class DynamicPriceStrategy(ChargingStrategy):
    """Dynamic price-based charging with intelligent threshold logic."""
    
    def __init__(self):
        """Initialize dynamic price strategy."""
        self.dynamic_analyzer = None
    
    def should_charge(self, context: Dict[str, Any]) -> Tuple[bool, str]:
        """Check if charging should occur based on dynamic price analysis."""
        price = context.get("price_analysis", {})
        battery = context.get("battery_analysis", {})
        config = context.get("config", {}) or {}
        settings = context.get("settings")

        current_price = price.get("current_price")
        if current_price is None:
            return False, ""

        threshold = price.get("price_threshold", 0.15)

        # Get base confidence from config (default 60%) and clamp to sensible range
        config_confidence = (
            getattr(settings, "dynamic_threshold_confidence", None)
            if settings is not None
            else None
        )
        if config_confidence is None:
            config_confidence = config.get("dynamic_threshold_confidence", DEFAULT_DYNAMIC_THRESHOLD_CONFIDENCE)
        try:
            config_confidence = float(config_confidence) / 100.0
        except (TypeError, ValueError):
            config_confidence = CONFIDENCE_BASE
        config_confidence = min(max(config_confidence, CONFIDENCE_RANGE_MIN), CONFIDENCE_RANGE_MAX)

        # Prepare SOC/solar adjustments before running the analyzer so reasons align
        average_soc = battery.get("average_soc", 50)
        power = context.get("power_analysis", {})
        has_significant_solar = power.get("significant_solar_surplus", False)
        solar_surplus = power.get("solar_surplus", 0)

        confidence_threshold = config_confidence

        # Adjust based on SOC (make it easier to charge when battery is low)
        if average_soc < LOW_SOC_THRESHOLD:
            confidence_threshold = max(CONFIDENCE_RANGE_MIN, confidence_threshold + CONFIDENCE_LOW_SOC_ADJUSTMENT)
        elif average_soc >= HIGH_SOC_THRESHOLD:
            confidence_threshold = min(CONFIDENCE_RANGE_MAX, confidence_threshold + CONFIDENCE_HIGH_SOC_ADJUSTMENT)

        # Adjust based on actual solar surplus
        # Significant surplus → need +10% more confidence (be picky, wait for solar)
        # No surplus → need -10% less confidence (less picky, no solar available)
        if has_significant_solar:
            confidence_threshold = min(CONFIDENCE_RANGE_MAX, confidence_threshold + CONFIDENCE_SOLAR_ADJUSTMENT)
            solar_context = f"significant solar surplus ({solar_surplus}W) - waiting for better prices"
        elif solar_surplus > 0:
            solar_context = f"minor solar surplus ({solar_surplus}W)"
        else:
            confidence_threshold = max(CONFIDENCE_RANGE_MIN, confidence_threshold - CONFIDENCE_SOLAR_ADJUSTMENT)
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
        self._last_trace: List[Dict[str, Any]] = []
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
        trace: List[Dict[str, Any]] = []
        price = context.get("price_analysis", {})
        current_price = price.get("current_price")
        threshold = price.get("price_threshold", 0.15)

        if current_price is not None and current_price > threshold:
            # Check if emergency charging applies
            battery = context.get("battery_analysis", {})
            config = context.get("config", {}) or {}
            settings = context.get("settings")
            average_soc = battery.get("average_soc", 100)
            emergency_threshold = (
                getattr(settings, "emergency_soc_threshold", None)
                if settings is not None
                else None
            )
            if emergency_threshold is None:
                emergency_threshold = config.get("emergency_soc_threshold", DEFAULT_EMERGENCY_SOC)
            guard_entry = {
                "strategy": "PriceThresholdGuard",
                "priority": 0,
                "should_charge": False,
                "reason": (
                    f"Price {current_price:.3f}€/kWh exceeds maximum threshold {threshold:.3f}€/kWh"
                ),
            }

            if average_soc < emergency_threshold:
                guard_entry.update(
                    {
                        "should_charge": True,
                        "reason": (
                            f"Emergency charge - SOC {average_soc:.0f}% < {emergency_threshold}% threshold, "
                            f"charging regardless of price ({current_price:.3f}€/kWh)"
                        ),
                    }
                )
                trace.append(guard_entry)
                self._last_trace = trace
                return True, guard_entry["reason"]

            trace.append(guard_entry)
            self._last_trace = trace
            return False, guard_entry["reason"]

        last_reason = ""

        for strategy in self.strategies:
            should_charge, reason = strategy.should_charge(context)
            entry = {
                "strategy": strategy.__class__.__name__,
                "priority": strategy.get_priority(),
                "should_charge": bool(should_charge),
                "reason": reason or "",
            }
            trace.append(entry)

            if should_charge:
                _LOGGER.debug("Strategy %s decided to charge: %s",
                            strategy.__class__.__name__, reason)
                self._last_trace = trace
                return True, reason
            elif reason:  # Strategy made a decision not to charge, but continue checking
                _LOGGER.debug("Strategy %s suggests not charging: %s (continuing evaluation)",
                            strategy.__class__.__name__, reason)
                last_reason = reason

        # If we got here, no strategy said to charge
        # Use the last reason provided, or generate default
        if last_reason:
            trace.append(
                {
                    "strategy": "AdvisoryReason",
                    "priority": 998,
                    "should_charge": False,
                    "reason": last_reason,
                }
            )
            self._last_trace = trace
            return False, last_reason

        # Default decision if no strategy provided a reason
        battery = context.get("battery_analysis", {})
        position = price.get("price_position")
        average_soc = battery.get("average_soc")

        # Default to 0 for display purposes if None
        if average_soc is None:
            average_soc = 0

        price_fragment = (
            f"{current_price:.3f}€/kWh" if current_price is not None else "unknown price"
        )
        position_fragment = (
            f"{position:.0%} of daily range" if position is not None else "unknown price position"
        )

        default_reason = (
            f"Price not favorable ({price_fragment}, {position_fragment}) for SOC {average_soc:.0f}%"
        )
        trace.append(
            {
                "strategy": "DefaultDecision",
                "priority": 999,
                "should_charge": False,
                "reason": default_reason,
            }
        )
        self._last_trace = trace
        return False, default_reason

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

    def get_last_trace(self) -> List[Dict[str, Any]]:
        """Return a copy of the most recent strategy evaluation trace."""
        return list(self._last_trace)
