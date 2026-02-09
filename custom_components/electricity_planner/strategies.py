"""Charging strategies for decision making."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any
import logging

from .const import (
    DEFAULT_DYNAMIC_THRESHOLD_CONFIDENCE,
    DEFAULT_EMERGENCY_SOC,
    DEFAULT_PREDICTIVE_CHARGING_MIN_SOC,
    DEFAULT_VERY_LOW_PRICE_THRESHOLD,
    DEFAULT_SOC_PRICE_MULTIPLIER_MAX,
    DEFAULT_SOC_BUFFER_TARGET,
)
from .defaults import DEFAULT_ALGORITHM_THRESHOLDS, calculate_soc_price_multiplier
from .dynamic_threshold import DynamicThresholdAnalyzer

_LOGGER = logging.getLogger(__name__)


class ChargingStrategy(ABC):
    """Abstract base class for charging strategies."""

    @abstractmethod
    def should_charge(self, context: dict[str, Any]) -> tuple[bool, str]:
        """Determine if charging should occur."""
        pass

    @abstractmethod
    def get_priority(self) -> int:
        """Get strategy priority (lower = higher priority)."""
        pass


# NOTE: EmergencyChargingStrategy was removed - emergency logic is handled
# by the PriceThresholdGuard in StrategyManager.evaluate() which can override
# the price threshold when SOC is critically low.


class SolarPriorityStrategy(ChargingStrategy):
    """Prefer solar charging over grid."""
    
    def should_charge(self, context: dict[str, Any]) -> tuple[bool, str]:
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
        if remaining_solar > 0 and average_soc >= max_soc - DEFAULT_ALGORITHM_THRESHOLDS.soc_buffer:
            return False, (f"Battery {average_soc:.0f}% nearly full with {remaining_solar}W "
                          f"solar surplus - preventing solar waste")
        
        return False, ""

    def get_priority(self) -> int:
        return 1  # Highest priority (after guard)


class PredictiveChargingStrategy(ChargingStrategy):
    """Skip charging if significant price drop is expected.

    This strategy runs early (priority 2) to potentially delay charging
    when a better price is coming soon. It only provides advisory "wait"
    signals - it never forces charging.
    """

    def should_charge(self, context: dict[str, Any]) -> tuple[bool, str]:
        """Check if we should wait for better prices."""
        price = context.get("price_analysis", {})
        battery = context.get("battery_analysis", {})
        config = context.get("config", {}) or {}
        settings = context.get("settings")

        # Only consider waiting if current price is already acceptable
        if not price.get("is_low_price", False):
            return False, ""

        # Only wait if there's a significant price drop coming
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

        # Wait for the price drop (advisory - continues evaluation)
        next_price = price.get("next_price", 0)
        return False, (f"SOC {average_soc:.0f}% sufficient - waiting for significant price drop "
                      f"next hour ({next_price:.3f}€/kWh)")

    def get_priority(self) -> int:
        return 2  # Early - can delay charging for better prices


class VeryLowPriceStrategy(ChargingStrategy):
    """Charge when price is in bottom percentage of daily range."""

    def should_charge(self, context: dict[str, Any]) -> tuple[bool, str]:
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


class SOCBufferChargingStrategy(ChargingStrategy):
    """Charge at acceptable prices when battery SOC is low to prevent peak issues.

    This strategy triggers when the price threshold has been relaxed due to low SOC.
    It recommends charging to build a buffer and prevent peak demand spikes.
    """

    def should_charge(self, context: dict[str, Any]) -> tuple[bool, str]:
        """Check if buffer charging should occur due to low SOC."""
        # Only triggers if threshold was relaxed (SOC multiplier > 1.0)
        if not context.get("threshold_relaxed", False):
            return False, ""

        price = context.get("price_analysis", {})
        battery = context.get("battery_analysis", {})

        current_price = price.get("current_price")
        if current_price is None:
            return False, ""

        # Get threshold info from context (set by StrategyManager.evaluate)
        effective_threshold = context.get("effective_threshold", 0.15)
        soc_multiplier = context.get("soc_price_multiplier", 1.0)
        base_threshold = price.get("price_threshold", 0.15)

        # Only trigger when price is BETWEEN base and effective threshold
        # If price is already below base threshold, let normal strategies handle it
        if current_price <= base_threshold:
            return False, ""  # Normal strategies can handle this better

        average_soc = battery.get("average_soc")
        if average_soc is None:
            return False, ""

        max_soc = battery.get("max_soc_threshold", 90)
        if average_soc >= max_soc:
            return False, ""  # Battery full, no need to charge

        # Charge because price is acceptable due to low SOC relaxation
        # This only runs when: base_threshold < current_price <= effective_threshold
        return True, (
            f"Buffer charging - SOC {average_soc:.0f}% is low, "
            f"accepting price {current_price:.3f}€/kWh "
            f"(threshold relaxed from {base_threshold:.3f} to {effective_threshold:.3f}€/kWh, "
            f"×{soc_multiplier:.2f} multiplier) to prevent peak demand"
        )

    def get_priority(self) -> int:
        return 4  # After VeryLowPriceStrategy (3), before DynamicPriceStrategy (5)


class SOCBasedChargingStrategy(ChargingStrategy):
    """Charging based on SOC levels and live solar availability."""

    def should_charge(self, context: dict[str, Any]) -> tuple[bool, str]:
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
        return 6  # Last - safety net


class DynamicPriceStrategy(ChargingStrategy):
    """Dynamic price-based charging with intelligent threshold logic."""
    
    def __init__(self):
        """Initialize dynamic price strategy."""
        self.dynamic_analyzer = None
    
    def should_charge(self, context: dict[str, Any]) -> tuple[bool, str]:
        """Check if charging should occur based on dynamic price analysis."""
        price = context.get("price_analysis", {})
        battery = context.get("battery_analysis", {})
        config = context.get("config", {}) or {}
        settings = context.get("settings")

        current_price = price.get("current_price")
        if current_price is None:
            return False, ""

        # Check if battery is already full - no need to charge
        average_soc = battery.get("average_soc")
        max_soc = battery.get("max_soc_threshold", 90)
        if average_soc is not None and average_soc >= max_soc:
            return False, f"Battery full ({average_soc:.0f}% ≥ {max_soc}%) - no grid charging needed"

        # Use stable threshold snapshot for current 15-min interval if available
        # This prevents threshold fluctuations from causing on/off cycles within same price period
        stable_threshold = context.get("battery_stable_threshold")
        base_threshold = price.get("price_threshold", 0.15)

        if stable_threshold is not None:
            threshold = stable_threshold
            if abs(stable_threshold - base_threshold) > 0.001:  # Only log if different
                _LOGGER.debug(
                    "Using stable threshold snapshot %.4f€/kWh for current interval (current calculated: %.4f€/kWh)",
                    stable_threshold,
                    base_threshold,
                )
        else:
            threshold = base_threshold

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
            config_confidence = 0.6
        config_confidence = min(max(config_confidence, 0.3), 0.9)

        # Get solar info for context messages (average_soc already fetched above)
        soc_available = average_soc is not None

        power = context.get("power_analysis", {})
        has_significant_solar = power.get("significant_solar_surplus", False)
        solar_surplus = power.get("solar_surplus", 0)

        # NOTE: SOC-based confidence adjustment was removed - the SOC price multiplier
        # in StrategyManager.evaluate() now handles low-SOC scenarios by relaxing
        # the price threshold instead. This avoids double-relaxation.
        confidence_threshold = config_confidence

        # Adjust based on actual solar surplus only
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
            # Build user-friendly reason without exposing confidence internals
            if not soc_available:
                soc_context = "battery SOC unavailable"
            elif average_soc < 40:
                soc_context = f"low battery ({average_soc:.0f}%)"
            elif average_soc >= 70:
                soc_context = f"high battery ({average_soc:.0f}%)"
            else:
                soc_context = f"battery at {average_soc:.0f}%"

            reason = f"{analysis['reason']} - {soc_context}, {solar_context}"
            return True, reason

        # Not charging: provide clear reason without confusing confidence percentages
        if has_significant_solar and (not soc_available or average_soc >= 40):
            return False, f"{analysis['reason']} - waiting for solar (surplus: {solar_surplus}W)"
        elif soc_available and average_soc >= 70:
            return False, f"{analysis['reason']} - battery already at {average_soc:.0f}%"
        else:
            return False, f"{analysis['reason']} - price conditions not optimal yet"
    
    def get_priority(self) -> int:
        return 5  # After solar, predictive, very low price, and SOC buffer


class StrategyManager:
    """Manage and execute charging strategies."""
    
    def __init__(self, use_dynamic_threshold: bool = True):
        """Initialize strategy manager."""
        self._last_trace: list[dict[str, Any]] = []
        # Core strategies in priority order
        # NOTE: EmergencyChargingStrategy was removed - emergency logic is handled
        # by the PriceThresholdGuard in evaluate() which overrides price threshold
        # when SOC is critically low.
        self.strategies = [
            SolarPriorityStrategy(),         # Priority 1: Prefer solar
            PredictiveChargingStrategy(),    # Priority 2: Delay for better prices
            VeryLowPriceStrategy(),          # Priority 3: Charge at excellent prices
            SOCBufferChargingStrategy(),     # Priority 4: Buffer charging at acceptable prices
        ]

        # Conditionally add dynamic or traditional strategies
        if use_dynamic_threshold:
            self.dynamic_price_strategy = DynamicPriceStrategy()
            self.strategies.append(self.dynamic_price_strategy)  # Priority 5
        else:
            self.dynamic_price_strategy = None

        # Add safety net strategy
        self.strategies.append(SOCBasedChargingStrategy())  # Priority 6: Safety net

        # Sort by priority
        self.strategies.sort(key=lambda s: s.get_priority())
    
    def evaluate(self, context: dict[str, Any]) -> tuple[bool, str]:
        """Evaluate all strategies and return decision.

        Strategy priority order:
        1. SolarPriorityStrategy - Prefer solar over grid
        2. PredictiveChargingStrategy - Delay for better prices (advisory)
        3. VeryLowPriceStrategy - Charge at excellent prices
        4. SOCBufferChargingStrategy - Buffer charging at acceptable prices when SOC low
        5. DynamicPriceStrategy - Dynamic price analysis with confidence
        6. SOCBasedChargingStrategy - Safety net for low SOC situations

        Evaluation rules:
        - If a strategy returns True (charge), evaluation stops immediately
        - If a strategy returns False with a reason, the reason is saved but evaluation continues
        - This allows lower-priority safety nets (SOCBased) to override advisory decisions

        The PriceThresholdGuard runs BEFORE all strategies and can:
        - Block charging when price exceeds threshold (with SOC-based relaxation)
        - Force emergency charging when SOC is critically low (overrides price)
        """
        # Hard stop: Price too high (unless emergency or SOC-based relaxation applies)
        trace: list[dict[str, Any]] = []
        price = context.get("price_analysis", {})
        current_price = price.get("current_price")
        stable_threshold = context.get("battery_stable_threshold")
        if stable_threshold is not None:
            base_threshold = stable_threshold
            calc_threshold = price.get("price_threshold")
            if calc_threshold is not None and abs(calc_threshold - stable_threshold) > 0.001:
                _LOGGER.debug(
                    "PriceThresholdGuard using stable snapshot %.4f€/kWh (current calculated: %.4f€/kWh)",
                    stable_threshold,
                    calc_threshold,
                )
        else:
            base_threshold = price.get("price_threshold", 0.15)

        # Get battery and settings for SOC-based threshold relaxation
        battery = context.get("battery_analysis", {})
        config = context.get("config", {}) or {}
        settings = context.get("settings")
        average_soc = battery.get("average_soc")

        # Get SOC multiplier settings
        emergency_threshold = (
            getattr(settings, "emergency_soc_threshold", None)
            if settings is not None
            else None
        )
        if emergency_threshold is None:
            emergency_threshold = config.get("emergency_soc_threshold", DEFAULT_EMERGENCY_SOC)

        soc_multiplier_max = (
            getattr(settings, "soc_price_multiplier_max", None)
            if settings is not None
            else None
        )
        if soc_multiplier_max is None:
            soc_multiplier_max = config.get("soc_price_multiplier_max", DEFAULT_SOC_PRICE_MULTIPLIER_MAX)

        soc_buffer_target = (
            getattr(settings, "soc_buffer_target", None)
            if settings is not None
            else None
        )
        if soc_buffer_target is None:
            soc_buffer_target = config.get("soc_buffer_target", DEFAULT_SOC_BUFFER_TARGET)

        # Calculate effective threshold with SOC-based relaxation
        if average_soc is not None:
            soc_multiplier = calculate_soc_price_multiplier(
                current_soc=average_soc,
                emergency_soc=emergency_threshold,
                buffer_target_soc=soc_buffer_target,
                max_multiplier=soc_multiplier_max,
            )
            effective_threshold = base_threshold * soc_multiplier
        else:
            # No SOC data - use base threshold without relaxation
            soc_multiplier = 1.0
            effective_threshold = base_threshold

        if current_price is not None and current_price > effective_threshold:
            # Price exceeds even the relaxed threshold

            # If battery SOC is unavailable, we cannot evaluate emergency override
            # Use conservative approach: block charging at high prices
            if average_soc is None:
                _LOGGER.debug(
                    "Price threshold guard: cannot evaluate emergency override without battery SOC, "
                    "blocking charge at high price %.3f€/kWh > %.3f€/kWh",
                    current_price, effective_threshold
                )
                return False, f"Price {current_price:.3f}€/kWh exceeds threshold {effective_threshold:.3f}€/kWh (battery SOC unavailable)"

            guard_entry = {
                "strategy": "PriceThresholdGuard",
                "priority": 0,
                "should_charge": False,
                "reason": (
                    f"Price {current_price:.3f}€/kWh exceeds maximum threshold "
                    f"{effective_threshold:.3f}€/kWh (base {base_threshold:.3f} × {soc_multiplier:.2f} SOC multiplier)"
                ),
            }

            if average_soc <= emergency_threshold:
                guard_entry.update(
                    {
                        "should_charge": True,
                        "reason": (
                            f"Emergency charge - SOC {average_soc:.0f}% ≤ {emergency_threshold}% threshold, "
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

        # Store effective threshold info in context for strategies to use
        context["effective_threshold"] = effective_threshold
        context["soc_price_multiplier"] = soc_multiplier
        context["threshold_relaxed"] = soc_multiplier > 1.0

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

    def get_dynamic_threshold(self, context: dict[str, Any]) -> float | None:
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

    def get_last_trace(self) -> list[dict[str, Any]]:
        """Return a copy of the most recent strategy evaluation trace."""
        return list(self._last_trace)
