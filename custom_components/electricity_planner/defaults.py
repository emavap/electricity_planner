"""Default configuration values and data classes for Electricity Planner."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

# Time-related constants
@dataclass
class TimeSchedule:
    """Time schedule configuration."""
    night_start: int = 22  # 10 PM
    night_end: int = 6     # 6 AM
    early_morning_end: int = 9
    solar_peak_start: int = 10
    solar_peak_end: int = 16
    evening_start: int = 17
    evening_end: int = 21

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
    critical_low_soc: int = 30  # % - Always charge below this
    low_soc_threshold: int = 40  # % - Low SOC for decision logic
    medium_soc_threshold: int = 50  # % - Medium SOC charging threshold
    high_soc_threshold: int = 60  # % - High SOC threshold for time-based
    max_target_soc: int = 90  # % - Maximum target SOC for calculations
    
    significant_price_drop: float = 0.15  # 15% - Price drop considered significant
    neutral_price_position: float = 0.5  # 50% - Neutral position in daily range
    
    poor_solar_threshold: float = 0.3  # 30% - Below this is poor
    moderate_solar_threshold: float = 0.6  # 60% - Above this is good
    excellent_solar_threshold: float = 0.8  # 80% - Above this is excellent

# System limits
@dataclass
class SystemLimits:
    """System power limits."""
    max_car_charger_power: int = 11000  # W - Maximum car charger power
    min_update_interval: int = 10  # seconds - Minimum between entity updates
    max_update_interval: int = 30  # seconds - Maximum update interval
    evaluation_interval: int = 5  # minutes - Decision re-evaluation interval
    data_unavailable_notification_delay: int = 60  # seconds - Delay before notification

# Winter months for seasonal logic
WINTER_MONTHS: Final[list[int]] = [11, 12, 1, 2]

# Create default instances
DEFAULT_TIME_SCHEDULE = TimeSchedule()
DEFAULT_POWER_ESTIMATES = PowerEstimates()
DEFAULT_ALGORITHM_THRESHOLDS = AlgorithmThresholds()
DEFAULT_SYSTEM_LIMITS = SystemLimits()
