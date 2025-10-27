"""Dynamic charging strategies for smarter threshold-based decisions."""
from __future__ import annotations

from typing import Dict, Optional


class DynamicThresholdAnalyzer:
    """Analyze price patterns to make smarter charging decisions within threshold."""

    def __init__(self, threshold: float, base_confidence: float = 0.60):
        """Initialize with maximum price threshold and base confidence requirement.

        Args:
            threshold: Maximum price threshold (€/kWh)
            base_confidence: Base confidence required to charge (0.0-1.0), default 0.60 (60%)
        """
        self.max_threshold = threshold
        self.base_confidence = base_confidence
        
    def analyze_price_window(
        self,
        current_price: float,
        highest_today: float,
        lowest_today: float,
        next_price: Optional[float] = None
    ) -> Dict[str, Any]:
        """Analyze if current price is good within the threshold range."""

        if current_price > self.max_threshold:
            return {
                "should_charge": False,
                "confidence": 0.0,
                "reason": f"Price {current_price:.3f}€/kWh exceeds maximum threshold {self.max_threshold:.3f}€/kWh"
            }

        # Calculate price quality within acceptable range
        # Price quality: 0% = at threshold (worst), 100% = at daily lowest (best)
        acceptable_range = self.max_threshold - lowest_today
        if acceptable_range > 0:
            price_quality = (self.max_threshold - current_price) / acceptable_range
        else:
            # No headroom left below the configured threshold (or all prices equal)
            acceptable_range = 0.0
            price_quality = 0.0

        # Dynamic threshold based on daily range
        # More selective when range is wide, less selective when narrow
        price_volatility = (highest_today - lowest_today) / highest_today if highest_today > 0 else 0

        # Calculate dynamic threshold within the acceptable range
        if price_volatility > 0.5:  # High volatility (>50% range)
            # Be very selective - only charge at bottom 40% of acceptable range
            dynamic_threshold = lowest_today + (acceptable_range * 0.4)
        elif price_volatility > 0.3:  # Medium volatility (30-50% range)
            # Moderately selective - charge at bottom 60% of acceptable range
            dynamic_threshold = lowest_today + (acceptable_range * 0.6)
        else:  # Low volatility (<30% range)
            # Less selective - charge at bottom 80% of acceptable range
            dynamic_threshold = lowest_today + (acceptable_range * 0.8)

        # Ensure the derived threshold never exceeds the configured maximum
        if dynamic_threshold > self.max_threshold:
            dynamic_threshold = self.max_threshold

        # Simple next-hour check (only if we have real data)
        next_hour_better = False
        if next_price is not None and next_price < current_price * 0.9:  # 10% better next hour
            next_hour_better = True

        # Calculate confidence score (0-1)
        confidence_factors = []

        # Factor 1: Price quality (0-1) - where we are in the acceptable range
        confidence_factors.append(price_quality)

        # Factor 2: Below dynamic threshold (0 or 1) - hard requirement
        below_dynamic_threshold = current_price <= dynamic_threshold
        confidence_factors.append(1.0 if below_dynamic_threshold else 0.0)

        # Factor 3: Not improving next hour (1 if stable/worse, 0.3 if improving)
        confidence_factors.append(0.3 if next_hour_better else 1.0)

        # Calculate weighted confidence
        weights = [0.4, 0.4, 0.2]  # Price quality and dynamic threshold most important
        confidence = sum(f * w for f, w in zip(confidence_factors, weights))

        # Enforce dynamic threshold ceiling on confidence
        if not below_dynamic_threshold:
            confidence = min(confidence, 0.25)

        # Decision based on confidence
        should_charge = below_dynamic_threshold and confidence >= self.base_confidence

        # Build reasoning
        if should_charge:
            if price_quality > 0.8:
                reason = f"Excellent price {current_price:.3f}€/kWh - in bottom 20% of acceptable range"
            elif price_quality > 0.6:
                reason = f"Good price {current_price:.3f}€/kWh - in bottom 40% of acceptable range"
            else:
                reason = f"Acceptable price {current_price:.3f}€/kWh - below dynamic threshold {dynamic_threshold:.3f}€/kWh"
        else:
            if next_hour_better:
                reason = f"Waiting - better price {next_price:.3f}€/kWh next hour (current: {current_price:.3f}€/kWh)"
            elif current_price > dynamic_threshold:
                reason = f"Price {current_price:.3f}€/kWh above dynamic threshold {dynamic_threshold:.3f}€/kWh"
            else:
                reason = f"Price {current_price:.3f}€/kWh not optimal (confidence: {confidence:.1%})"

        return {
            "should_charge": should_charge,
            "confidence": confidence,
            "reason": reason,
            "price_quality": price_quality,
            "dynamic_threshold": dynamic_threshold,
            "price_volatility": price_volatility,
            "next_hour_better": next_hour_better,
        }
