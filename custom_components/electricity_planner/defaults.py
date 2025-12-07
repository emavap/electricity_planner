"""Default configuration values and data classes for Electricity Planner."""
from __future__ import annotations

from dataclasses import dataclass

# Power/Energy estimation constants
@dataclass
class PowerEstimates:
    """Power and energy estimation configuration."""
    per_soc_percent: float = 100.0  # W per 1% SOC
    kwh_per_soc_percent: float = 0.1  # kWh per 1% SOC
    max_solar_production: float = 5000.0  # W - Assumed max for efficiency calc
    typical_daily_solar_min: float = 5.0  # kWh - Minimum for decent solar day
    good_hourly_solar: float = 2.0  # kWh/hour - Good production
    house_consumption_factor: float = 0.5  # 50% - Estimated surplus factor
    default_battery_capacity: float = 10.0  # kWh - Default if not configured

# Algorithm thresholds
@dataclass
class AlgorithmThresholds:
    """Algorithm decision thresholds."""
    soc_safety_margin: int = 5  # % - Safety margin to prevent waste
    soc_buffer: int = 10  # % - Buffer for various SOC checks
    low_soc_threshold: int = 40  # % - Low SOC for decision logic
    medium_soc_threshold: int = 50  # % - Medium SOC charging threshold
    high_soc_threshold: int = 60  # % - High SOC threshold for time-based
    max_target_soc: int = 90  # % - Maximum target SOC for calculations

    significant_price_drop: float = 0.15  # 15% - Price drop considered significant
    neutral_price_position: float = 0.5  # 50% - Neutral position in daily range

# System limits
@dataclass
class SystemLimits:
    """System power limits."""
    max_car_charger_power: int = 11000  # W - Maximum car charger power
    min_update_interval: int = 10  # seconds - Minimum between entity updates
    max_update_interval: int = 30  # seconds - Maximum update interval
    evaluation_interval: int = 5  # minutes - Decision re-evaluation interval
    data_unavailable_notification_delay: int = 60  # seconds - Delay before notification

# Create default instances
DEFAULT_POWER_ESTIMATES = PowerEstimates()
DEFAULT_ALGORITHM_THRESHOLDS = AlgorithmThresholds()
DEFAULT_SYSTEM_LIMITS = SystemLimits()


def calculate_soc_price_multiplier(
    current_soc: float,
    emergency_soc: float,
    buffer_target_soc: float,
    max_multiplier: float,
) -> float:
    """Calculate price threshold multiplier based on battery SOC.

    This implements a sliding scale that relaxes price requirements when
    battery SOC is low, helping prevent peak demand issues by allowing
    charging at "acceptable" prices rather than waiting for "optimal" ones.

    Args:
        current_soc: Current battery state of charge (%)
        emergency_soc: Emergency SOC threshold (%)
        buffer_target_soc: Target SOC above which no relaxation is applied (%)
        max_multiplier: Maximum multiplier at emergency SOC (e.g., 1.3 = 130%)

    Returns:
        A multiplier between 1.0 and max_multiplier:
        - At or above buffer_target_soc: returns 1.0 (no relaxation)
        - At or below emergency_soc: returns max_multiplier (maximum relaxation)
        - Between: linear interpolation

    Example with emergency=15%, buffer=50%, max_multiplier=1.3:
        - At 15% SOC → multiplier = 1.3 (accept 30% higher prices)
        - At 32.5% SOC → multiplier = 1.15
        - At 50% SOC → multiplier = 1.0
        - At 70% SOC → multiplier = 1.0
    """
    # Validate inputs
    if max_multiplier < 1.0:
        max_multiplier = 1.0

    # Above buffer target: no price relaxation needed
    if current_soc >= buffer_target_soc:
        return 1.0

    # At or below emergency: maximum relaxation
    if current_soc <= emergency_soc:
        return max_multiplier

    # Linear interpolation between emergency and buffer target
    soc_range = buffer_target_soc - emergency_soc
    if soc_range <= 0:
        # Invalid configuration - buffer should be > emergency
        return 1.0

    # How far are we from the buffer target (as a ratio 0-1)?
    # At emergency_soc this is 1.0, at buffer_target this is 0.0
    position = (buffer_target_soc - current_soc) / soc_range

    # Calculate multiplier: 1.0 at buffer, max_multiplier at emergency
    return 1.0 + (max_multiplier - 1.0) * position
