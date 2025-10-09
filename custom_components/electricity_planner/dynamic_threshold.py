"""Dynamic charging strategies for smarter threshold-based decisions."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import statistics

from .defaults import DEFAULT_ALGORITHM_THRESHOLDS

_LOGGER = logging.getLogger(__name__)


class DynamicThresholdAnalyzer:
    """Analyze price patterns to make smarter charging decisions within threshold."""
    
    def __init__(self, threshold: float):
        """Initialize with maximum price threshold."""
        self.max_threshold = threshold
        self.history = []
        
    def analyze_price_window(
        self,
        current_price: float,
        highest_today: float,
        lowest_today: float,
        next_price: Optional[float] = None,
        next_6h_prices: Optional[List[float]] = None
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
            price_quality = 1.0  # All prices equal, charge anytime
        
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
        
        # Look ahead analysis
        improving_soon = False
        if next_6h_prices:
            # Find the best price in the next 6 hours
            best_upcoming = min(next_6h_prices)
            avg_upcoming = statistics.mean(next_6h_prices)
            
            # Check if significantly better prices are coming
            if best_upcoming < current_price * 0.8:  # 20% better price coming
                improving_soon = True
            
            # Check if current price is better than upcoming average
            current_vs_future = current_price < avg_upcoming * 0.95
        else:
            current_vs_future = True  # No future data, can't compare
            best_upcoming = next_price if next_price else current_price
        
        # Calculate confidence score (0-1)
        confidence_factors = []
        
        # Factor 1: Price quality (0-1)
        confidence_factors.append(price_quality)
        
        # Factor 2: Below dynamic threshold (0 or 1)
        confidence_factors.append(1.0 if current_price <= dynamic_threshold else 0.3)
        
        # Factor 3: Not improving soon (0 or 1)
        confidence_factors.append(0.2 if improving_soon else 1.0)
        
        # Factor 4: Better than future average (0 or 1)
        confidence_factors.append(1.0 if current_vs_future else 0.5)
        
        # Factor 5: Absolute price level (lower is better)
        # Normalize current price: 0€ = 1.0 confidence, threshold = 0.5 confidence
        price_confidence = 1.0 - (current_price / (self.max_threshold * 2))
        confidence_factors.append(max(0, min(1, price_confidence)))
        
        # Calculate weighted confidence
        weights = [0.25, 0.25, 0.20, 0.15, 0.15]  # Price quality and dynamic threshold most important
        confidence = sum(f * w for f, w in zip(confidence_factors, weights))
        
        # Decision based on confidence
        should_charge = confidence >= 0.6  # Need 60% confidence to charge
        
        # Build reasoning
        if should_charge:
            if price_quality > 0.8:
                reason = f"Excellent price {current_price:.3f}€/kWh - in bottom 20% of acceptable range"
            elif price_quality > 0.6:
                reason = f"Good price {current_price:.3f}€/kWh - in bottom 40% of acceptable range"
            else:
                reason = f"Acceptable price {current_price:.3f}€/kWh - below dynamic threshold {dynamic_threshold:.3f}€/kWh"
        else:
            if improving_soon:
                reason = f"Waiting - better price {best_upcoming:.3f}€/kWh expected soon (current: {current_price:.3f}€/kWh)"
            elif current_price > dynamic_threshold:
                reason = f"Price {current_price:.3f}€/kWh above dynamic threshold {dynamic_threshold:.3f}€/kWh for current volatility"
            else:
                reason = f"Price {current_price:.3f}€/kWh not optimal (confidence: {confidence:.1%})"
        
        return {
            "should_charge": should_charge,
            "confidence": confidence,
            "reason": reason,
            "price_quality": price_quality,
            "dynamic_threshold": dynamic_threshold,
            "price_volatility": price_volatility,
            "improving_soon": improving_soon,
            "factors": {
                "price_quality": confidence_factors[0],
                "below_dynamic": confidence_factors[1],
                "not_improving": confidence_factors[2],
                "better_than_future": confidence_factors[3],
                "absolute_level": confidence_factors[4],
            }
        }


class AdaptiveChargingStrategy:
    """Adaptive strategy that learns from price patterns."""
    
    def __init__(self):
        """Initialize adaptive strategy."""
        self.daily_patterns = {}  # Store daily price patterns
        self.charge_history = []  # Track charging decisions and outcomes
        
    def update_daily_pattern(self, date: str, prices: List[float]):
        """Update daily price pattern for learning."""
        if not prices:
            return
            
        self.daily_patterns[date] = {
            "min": min(prices),
            "max": max(prices),
            "mean": statistics.mean(prices),
            "stdev": statistics.stdev(prices) if len(prices) > 1 else 0,
            "lowest_hours": [i for i, p in enumerate(prices) if p == min(prices)],
            "percentiles": {
                10: self._percentile(prices, 10),
                25: self._percentile(prices, 25),
                50: self._percentile(prices, 50),
                75: self._percentile(prices, 75),
                90: self._percentile(prices, 90),
            }
        }
    
    def _percentile(self, data: List[float], percentile: int) -> float:
        """Calculate percentile value."""
        if not data:
            return 0
        sorted_data = sorted(data)
        index = (len(sorted_data) - 1) * percentile / 100
        lower = sorted_data[int(index)]
        upper = sorted_data[min(int(index) + 1, len(sorted_data) - 1)]
        return lower + (upper - lower) * (index - int(index))
    
    def get_adaptive_threshold(self, base_threshold: float, current_hour: int) -> float:
        """Calculate adaptive threshold based on historical patterns."""
        if len(self.daily_patterns) < 7:
            # Not enough history, use base threshold
            return base_threshold * 0.8  # Be more selective initially
        
        # Analyze patterns from last 7 days
        recent_patterns = list(self.daily_patterns.values())[-7:]
        
        # Calculate typical price levels
        all_percentile_25 = [p["percentiles"][25] for p in recent_patterns]
        avg_25th_percentile = statistics.mean(all_percentile_25)
        
        # Time-based adjustment
        typical_low_hours = []
        for pattern in recent_patterns:
            typical_low_hours.extend(pattern["lowest_hours"])
        
        # Most common low price hours
        if typical_low_hours:
            from collections import Counter
            hour_frequency = Counter(typical_low_hours)
            most_common_hours = [h for h, _ in hour_frequency.most_common(6)]
            
            # Adjust threshold based on time
            if current_hour in most_common_hours:
                # This is typically a low-price hour, be more selective
                adaptive_threshold = min(avg_25th_percentile, base_threshold * 0.6)
            else:
                # Not typically a low hour, be less selective
                adaptive_threshold = min(base_threshold * 0.9, avg_25th_percentile * 1.2)
        else:
            adaptive_threshold = avg_25th_percentile
        
        return min(adaptive_threshold, base_threshold)  # Never exceed base threshold
    
    def should_charge_adaptive(
        self,
        current_price: float,
        base_threshold: float,
        current_hour: int,
        battery_soc: float
    ) -> Tuple[bool, str]:
        """Make adaptive charging decision based on learned patterns."""
        adaptive_threshold = self.get_adaptive_threshold(base_threshold, current_hour)
        
        if current_price > base_threshold:
            return False, f"Price {current_price:.3f}€/kWh exceeds maximum threshold"
        
        if current_price > adaptive_threshold:
            return False, f"Price {current_price:.3f}€/kWh above adaptive threshold {adaptive_threshold:.3f}€/kWh for this time period"
        
        # SOC-based urgency adjustment
        urgency_factor = max(0.5, min(1.0, (100 - battery_soc) / 50))  # More urgent when SOC low
        urgency_adjusted_threshold = adaptive_threshold + (base_threshold - adaptive_threshold) * (1 - urgency_factor)
        
        if current_price <= urgency_adjusted_threshold:
            return True, f"Price {current_price:.3f}€/kWh within adaptive threshold {urgency_adjusted_threshold:.3f}€/kWh (SOC urgency: {urgency_factor:.0%})"
        
        return False, f"Price {current_price:.3f}€/kWh not optimal for current patterns"


class PriceRankingStrategy:
    """Strategy based on price ranking within time windows."""
    
    def __init__(self, window_hours: int = 24):
        """Initialize with ranking window."""
        self.window_hours = window_hours
        
    def rank_current_price(
        self,
        current_price: float,
        price_history: List[Tuple[datetime, float]],
        price_forecast: List[Tuple[datetime, float]]
    ) -> Dict[str, Any]:
        """Rank current price within window."""
        now = datetime.now()
        window_start = now - timedelta(hours=self.window_hours // 2)
        window_end = now + timedelta(hours=self.window_hours // 2)
        
        # Collect prices in window
        window_prices = []
        
        # Add historical prices
        for timestamp, price in price_history:
            if window_start <= timestamp <= now:
                window_prices.append(price)
        
        # Add current
        window_prices.append(current_price)
        
        # Add forecast
        for timestamp, price in price_forecast:
            if now < timestamp <= window_end:
                window_prices.append(price)
        
        if not window_prices:
            return {
                "rank_percentile": 50,
                "is_good_time": True,
                "reason": "No comparison data available"
            }
        
        # Calculate ranking
        sorted_prices = sorted(window_prices)
        rank = sorted_prices.index(current_price) if current_price in sorted_prices else len(sorted_prices) // 2
        rank_percentile = (rank / len(sorted_prices)) * 100
        
        # Determine if good time
        is_good_time = rank_percentile <= 40  # Bottom 40% of window
        
        if is_good_time:
            if rank_percentile <= 10:
                reason = f"Excellent - price in bottom 10% of {self.window_hours}h window"
            elif rank_percentile <= 25:
                reason = f"Very good - price in bottom 25% of {self.window_hours}h window"
            else:
                reason = f"Good - price in bottom 40% of {self.window_hours}h window"
        else:
            reason = f"Not optimal - price at {rank_percentile:.0f}% percentile in {self.window_hours}h window"
        
        return {
            "rank_percentile": rank_percentile,
            "is_good_time": is_good_time,
            "reason": reason,
            "window_size": len(window_prices),
            "better_count": rank,
            "worse_count": len(window_prices) - rank - 1
        }


class SmartChargingDecisionEngine:
    """Enhanced decision engine with dynamic threshold logic."""
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize smart decision engine."""
        self.config = config
        self.threshold = config.get("price_threshold", 0.15)
        self.dynamic_analyzer = DynamicThresholdAnalyzer(self.threshold)
        self.adaptive_strategy = AdaptiveChargingStrategy()
        self.ranking_strategy = PriceRankingStrategy()
        
    def make_charging_decision(
        self,
        current_price: float,
        price_data: Dict[str, Any],
        battery_soc: float,
        context: Dict[str, Any]
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """Make smart charging decision with dynamic threshold."""
        
        # Never charge above absolute threshold
        if current_price > self.threshold:
            return False, f"Price {current_price:.3f}€/kWh exceeds maximum threshold {self.threshold:.3f}€/kWh", {}
        
        # Analyze with dynamic threshold
        dynamic_analysis = self.dynamic_analyzer.analyze_price_window(
            current_price=current_price,
            highest_today=price_data.get("highest_price", current_price),
            lowest_today=price_data.get("lowest_price", current_price),
            next_price=price_data.get("next_price"),
            next_6h_prices=price_data.get("next_6h_prices", [])
        )
        
        # Check ranking
        ranking = self.ranking_strategy.rank_current_price(
            current_price=current_price,
            price_history=context.get("price_history", []),
            price_forecast=context.get("price_forecast", [])
        )
        
        # Combine strategies
        should_charge = False
        reasons = []
        
        # High confidence from dynamic analysis
        if dynamic_analysis["confidence"] > 0.75:
            should_charge = True
            reasons.append(dynamic_analysis["reason"])
        
        # Good ranking position
        elif ranking["rank_percentile"] <= 25:
            should_charge = True
            reasons.append(ranking["reason"])
        
        # Medium confidence but low SOC
        elif dynamic_analysis["confidence"] > 0.5 and battery_soc < 40:
            should_charge = True
            reasons.append(f"SOC {battery_soc:.0f}% low + {dynamic_analysis['reason']}")
        
        # Not good enough
        else:
            reasons.append(dynamic_analysis["reason"])
        
        # Build detailed info
        details = {
            "dynamic_threshold": dynamic_analysis["dynamic_threshold"],
            "confidence": dynamic_analysis["confidence"],
            "price_quality": dynamic_analysis["price_quality"],
            "rank_percentile": ranking["rank_percentile"],
            "factors": dynamic_analysis["factors"]
        }
        
        return should_charge, " | ".join(reasons), details
