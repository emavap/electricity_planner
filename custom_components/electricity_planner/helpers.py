"""Helper functions and utilities for Electricity Planner."""
from __future__ import annotations

import logging
from datetime import datetime
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)


class DataValidator:
    """Validate and sanitize data."""
    
    @staticmethod
    def validate_power_value(
        power: float,
        min_value: float = 0,
        max_value: Optional[float] = None,
        name: str = "power"
    ) -> float:
        """Validate and clamp power values to safe ranges."""
        if power < min_value:
            _LOGGER.warning("%s value %sW below minimum, clamping to %sW", 
                          name, power, min_value)
            return min_value
        if max_value is not None and power > max_value:
            _LOGGER.warning("%s value %sW above maximum, clamping to %sW", 
                          name, power, max_value)
            return max_value
        return power
    
    @staticmethod
    def validate_battery_data(battery_soc_data: List[Dict]) -> Tuple[bool, str]:
        """Validate battery data integrity."""
        if not battery_soc_data:
            return False, "No battery entities configured"
        
        valid_count = sum(1 for b in battery_soc_data 
                         if b.get("soc") is not None and 0 <= b["soc"] <= 100)
        
        if valid_count == 0:
            return False, "All battery sensors returning invalid data"
        
        if valid_count < len(battery_soc_data) / 2:
            _LOGGER.warning("More than 50% of battery sensors unavailable")
        
        return True, f"{valid_count}/{len(battery_soc_data)} sensors valid"
    
    @staticmethod
    def sanitize_config_value(
        value: Any,
        min_val: float,
        max_val: float,
        default: float,
        name: str = "config"
    ) -> float:
        """Sanitize configuration values to prevent issues."""
        try:
            val = float(value)
            if not min_val <= val <= max_val:
                _LOGGER.warning(
                    "%s value %s out of range [%s, %s], using default %s",
                    name, val, min_val, max_val, default
                )
                return default
            return val
        except (TypeError, ValueError):
            _LOGGER.error("%s value %s invalid, using default %s", 
                         name, value, default)
            return default
    
    @staticmethod
    def is_valid_state(state: Any) -> bool:
        """Check if a state value is valid."""
        return state not in (None, STATE_UNAVAILABLE, STATE_UNKNOWN, "")


def apply_price_adjustment(
    price: Optional[float],
    multiplier: float = 1.0,
    offset: float = 0.0
) -> Optional[float]:
    """Apply a simple affine transformation to a price value."""
    if price is None:
        return None
    try:
        return (float(price) * float(multiplier)) + float(offset)
    except (TypeError, ValueError):
        _LOGGER.error("Invalid price adjustment: price=%s, multiplier=%s, offset=%s", price, multiplier, offset)
        return None


class PriceCalculator:
    """Price-related calculations."""
    
    @staticmethod
    @lru_cache(maxsize=128)
    def calculate_price_position(
        current: float,
        highest: float,
        lowest: float
    ) -> float:
        """Calculate price position relative to daily range (cached)."""
        if highest > lowest:
            return (current - lowest) / (highest - lowest)
        return 0.5  # Neutral if no valid range
    
    @staticmethod
    def is_significant_price_drop(
        current_price: float,
        next_price: Optional[float],
        threshold: float = 0.15
    ) -> bool:
        """Check if there's a significant price drop coming."""
        if next_price is None or current_price <= 0:
            return False
        return (current_price - next_price) / current_price > threshold


class TimeContext:
    """Time-based context utilities."""
    
    @staticmethod
    def get_current_context(
        night_start: int = 22,
        night_end: int = 6,
        solar_peak_start: int = 10,
        solar_peak_end: int = 16,
        evening_start: int = 17,
        evening_end: int = 21
    ) -> Dict[str, Any]:
        """Get time-of-day context for charging decisions."""
        now = dt_util.now()
        hour = now.hour

        return {
            "current_hour": hour,
            "is_night": hour >= night_start or hour <= night_end,
            "is_early_morning": night_end < hour <= 9,
            "is_solar_peak": solar_peak_start <= hour <= solar_peak_end,
            "is_evening": evening_start <= hour <= evening_end,
            "hours_until_sunrise": max(0, (night_end - hour) % 24),
            "winter_season": now.month in [11, 12, 1, 2],
            "timestamp": now.isoformat(),
        }
    
    @staticmethod
    def is_within_time_window(
        start_hour: int,
        end_hour: int,
        current_hour: Optional[int] = None
    ) -> bool:
        """Check if current time is within specified window."""
        if current_hour is None:
            current_hour = dt_util.now().hour
        
        if start_hour <= end_hour:
            return start_hour <= current_hour <= end_hour
        else:  # Spans midnight
            return current_hour >= start_hour or current_hour <= end_hour


class CircuitBreaker:
    """Circuit breaker to prevent cascading failures."""
    
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        name: str = "default"
    ):
        """Initialize circuit breaker."""
        self.name = name
        self.failure_count = 0
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.last_failure_time: Optional[datetime] = None
        self.state = "closed"  # closed, open, half-open
    
    def call(self, func, *args, **kwargs) -> Any:
        """Execute function with circuit breaker protection."""
        if self.state == "open":
            if self._should_attempt_reset():
                self.state = "half-open"
                _LOGGER.info("Circuit breaker %s entering half-open state", self.name)
            else:
                raise Exception(f"Circuit breaker {self.name} is open")
        
        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise e
    
    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset."""
        if self.last_failure_time is None:
            return False
        return (dt_util.utcnow() - self.last_failure_time).seconds >= self.recovery_timeout
    
    def _on_success(self):
        """Handle successful call."""
        if self.state == "half-open":
            _LOGGER.info("Circuit breaker %s recovered, closing", self.name)
        self.failure_count = 0
        self.state = "closed"
    
    def _on_failure(self):
        """Handle failed call."""
        self.failure_count += 1
        self.last_failure_time = dt_util.utcnow()
        
        if self.failure_count >= self.failure_threshold:
            self.state = "open"
            _LOGGER.warning(
                "Circuit breaker %s opened after %d failures",
                self.name, self.failure_count
            )
        elif self.state == "half-open":
            self.state = "open"
            _LOGGER.warning("Circuit breaker %s reopened on failure", self.name)


class PowerAllocationValidator:
    """Validate power allocation logic."""
    
    @staticmethod
    def validate_allocation(
        allocation: Dict[str, Any],
        available_solar: float,
        max_battery_power: float,
        max_car_power: float
    ) -> Tuple[bool, Optional[str]]:
        """Validate power allocation doesn't exceed limits."""
        solar_for_batteries = allocation.get("solar_for_batteries", 0)
        solar_for_car = allocation.get("solar_for_car", 0)
        car_current_usage = allocation.get("car_current_solar_usage", 0)
        total_allocated = allocation.get("total_allocated", 0)
        
        # Check individual limits
        if solar_for_batteries > max_battery_power:
            return False, f"Battery allocation {solar_for_batteries}W exceeds limit {max_battery_power}W"
        
        if solar_for_car > max_car_power:
            return False, f"Car allocation {solar_for_car}W exceeds limit {max_car_power}W"
        
        # Check total doesn't exceed available
        calculated_total = solar_for_batteries + solar_for_car + car_current_usage
        if calculated_total > available_solar * 1.1:  # 10% tolerance
            return False, f"Total allocation {calculated_total}W exceeds available {available_solar}W"
        
        # Check internal consistency
        if abs(calculated_total - total_allocated) > 1:
            return False, f"Allocation mismatch: sum={calculated_total}W, total={total_allocated}W"
        
        return True, None


def format_reason(
    action: str,
    primary_reason: str,
    details: Optional[Dict[str, Any]] = None
) -> str:
    """Format a decision reason with optional details."""
    reason = f"{action}: {primary_reason}"
    
    if details:
        detail_parts = []
        for key, value in details.items():
            if isinstance(value, float):
                if "price" in key.lower() or "€" in str(value):
                    detail_parts.append(f"{key}={value:.3f}€/kWh")
                elif "soc" in key.lower() or "%" in str(value):
                    detail_parts.append(f"{key}={value:.0f}%")
                else:
                    detail_parts.append(f"{key}={value:.0f}W")
            else:
                detail_parts.append(f"{key}={value}")
        
        if detail_parts:
            reason += f" ({', '.join(detail_parts)})"
    
    return reason
