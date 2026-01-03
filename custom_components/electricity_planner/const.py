"""Constants for the Electricity Planner integration."""

DOMAIN = "electricity_planner"

CONF_PHASE_MODE = "phase_mode"
PHASE_MODE_SINGLE = "single_phase"
PHASE_MODE_THREE = "three_phase"
CONF_PHASES = "phases"
PHASE_IDS = ("phase_1", "phase_2", "phase_3")
DEFAULT_PHASE_NAMES = {
    "phase_1": "Phase 1",
    "phase_2": "Phase 2",
    "phase_3": "Phase 3",
}
CONF_PHASE_NAME = "name"
CONF_PHASE_SOLAR_ENTITY = "solar_entity"
CONF_PHASE_CONSUMPTION_ENTITY = "consumption_entity"
CONF_PHASE_CAR_ENTITY = "car_entity"
CONF_PHASE_BATTERY_POWER_ENTITY = "battery_power_entity"  # Actual battery power on this phase (W, negative = charging)

CONF_NORDPOOL_CONFIG_ENTRY = "nordpool_config_entry"
CONF_CURRENT_PRICE_ENTITY = "current_price_entity"
CONF_HIGHEST_PRICE_ENTITY = "highest_price_entity"
CONF_LOWEST_PRICE_ENTITY = "lowest_price_entity"
CONF_NEXT_PRICE_ENTITY = "next_price_entity"
CONF_BATTERY_SOC_ENTITIES = "battery_soc_entities"
CONF_BATTERY_CAPACITIES = "battery_capacities"
CONF_BATTERY_PHASE_ASSIGNMENTS = "battery_phase_assignments"
CONF_SOLAR_PRODUCTION_ENTITY = "solar_production_entity"
CONF_HOUSE_CONSUMPTION_ENTITY = "house_consumption_entity"
CONF_CAR_CHARGING_POWER_ENTITY = "car_charging_power_entity"
CONF_MONTHLY_GRID_PEAK_ENTITY = "monthly_grid_peak_entity"
CONF_TRANSPORT_COST_ENTITY = "transport_cost_entity"
CONF_GRID_POWER_ENTITY = "grid_power_entity"

CONF_MIN_SOC_THRESHOLD = "min_soc_threshold"
CONF_MAX_SOC_THRESHOLD = "max_soc_threshold"
CONF_PRICE_THRESHOLD = "price_threshold"
CONF_EMERGENCY_SOC_THRESHOLD = "emergency_soc_threshold"
CONF_VERY_LOW_PRICE_THRESHOLD = "very_low_price_threshold"
CONF_SIGNIFICANT_SOLAR_THRESHOLD = "significant_solar_threshold"
CONF_FEEDIN_PRICE_THRESHOLD = "feedin_price_threshold"

# Safety Limits Configuration Keys
CONF_MAX_BATTERY_POWER = "max_battery_power"
CONF_MAX_CAR_POWER = "max_car_power"
CONF_MAX_GRID_POWER = "max_grid_power"
CONF_MIN_CAR_CHARGING_THRESHOLD = "min_car_charging_threshold"
CONF_PREDICTIVE_CHARGING_MIN_SOC = "predictive_charging_min_soc"
CONF_BASE_GRID_SETPOINT = "base_grid_setpoint"
CONF_USE_DYNAMIC_THRESHOLD = "use_dynamic_threshold"
CONF_DYNAMIC_THRESHOLD_CONFIDENCE = "dynamic_threshold_confidence"
CONF_USE_AVERAGE_THRESHOLD = "use_average_threshold"
CONF_MIN_CAR_CHARGING_DURATION = "min_car_charging_duration"
CONF_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER = "car_permissive_threshold_multiplier"
CONF_PRICE_ADJUSTMENT_MULTIPLIER = "price_adjustment_multiplier"
CONF_PRICE_ADJUSTMENT_OFFSET = "price_adjustment_offset"
CONF_FEEDIN_ADJUSTMENT_MULTIPLIER = "feedin_adjustment_multiplier"
CONF_FEEDIN_ADJUSTMENT_OFFSET = "feedin_adjustment_offset"
CONF_SOC_PRICE_MULTIPLIER_MAX = "soc_price_multiplier_max"
CONF_SOC_BUFFER_TARGET = "soc_buffer_target"

# Default Threshold Values
DEFAULT_MIN_SOC = 20
DEFAULT_MAX_SOC = 90
DEFAULT_PRICE_THRESHOLD = 0.15
DEFAULT_EMERGENCY_SOC = 15
DEFAULT_VERY_LOW_PRICE_THRESHOLD = 30  # Bottom 30% of daily range
DEFAULT_SIGNIFICANT_SOLAR_THRESHOLD = 1000  # 1kW
DEFAULT_FEEDIN_PRICE_THRESHOLD = 0.05  # €0.05/kWh - export only above this price

# Default Safety Limits (W)
DEFAULT_MAX_BATTERY_POWER = 3000  # 3kW typical home battery inverter limit
DEFAULT_MAX_CAR_POWER = 11000  # 11kW typical home car charger limit
DEFAULT_MAX_GRID_POWER = 15000  # 15kW typical home grid connection limit
DEFAULT_MIN_CAR_CHARGING_THRESHOLD = 100  # Minimum power to consider car "charging"
DEFAULT_PREDICTIVE_CHARGING_MIN_SOC = 30  # Minimum SOC for predictive charging logic
DEFAULT_USE_DYNAMIC_THRESHOLD = False  # Use intelligent dynamic threshold logic (opt-in)
DEFAULT_DYNAMIC_THRESHOLD_CONFIDENCE = 75  # Default confidence threshold (75% - more aggressive)
DEFAULT_USE_AVERAGE_THRESHOLD = False  # Use average of future prices as threshold (opt-in)
DEFAULT_MIN_CAR_CHARGING_DURATION = 2  # Minimum hours of low prices to start car charging
DEFAULT_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER = 1.2  # 20% higher threshold when permissive mode active
DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER = 1.0  # No adjustment by default
DEFAULT_PRICE_ADJUSTMENT_OFFSET = 0.0  # €/kWh offset
DEFAULT_FEEDIN_ADJUSTMENT_MULTIPLIER = 1.0  # No adjustment by default
DEFAULT_FEEDIN_ADJUSTMENT_OFFSET = 0.0  # €/kWh offset
DEFAULT_SOC_PRICE_MULTIPLIER_MAX = 1.3  # Accept prices up to 130% of threshold when battery is critically low
DEFAULT_SOC_BUFFER_TARGET = 50  # Target SOC % above which no price relaxation is applied

# Algorithm Constants
DEFAULT_BASE_GRID_SETPOINT = 2500  # Conservative base grid limit (W)
DEFAULT_MONTHLY_PEAK_SAFETY_MARGIN = 0.9  # Use 90% of monthly peak

# Cache and Performance Constants
NORDPOOL_CACHE_MAX_SIZE = 10  # Maximum number of cached Nord Pool price entries
NORDPOOL_CACHE_TTL_MINUTES = 5  # Cache time-to-live in minutes

# Time-based Constants (extracted from magic numbers)
PRICE_INTERVAL_LOOKBACK_HOURS = 1  # How far back to look for price intervals
PEAK_MONITORING_DURATION_MINUTES = 5  # Duration to monitor before triggering peak limit
PEAK_LIMIT_DURATION_MINUTES = 15  # Duration of peak limit once triggered
PRICE_INTERVAL_MINUTES = 15  # Default price interval duration
PRICE_INTERVAL_GAP_TOLERANCE_SECONDS = 30  # Tolerance for gaps between intervals (increased from 5s)

# Validation Constants
PRICE_VALUE_MIN_EUR_MWH = -1000  # Minimum reasonable price in €/MWh (negative prices are valid)
PRICE_VALUE_MAX_EUR_MWH = 10000  # Maximum reasonable price in €/MWh
PERMISSIVE_MULTIPLIER_MIN = 1.0  # Minimum permissive mode multiplier
PERMISSIVE_MULTIPLIER_MAX = 2.0  # Maximum permissive mode multiplier (200% of base)
BATTERY_SOC_DECIMAL_THRESHOLD = 1.0  # If SOC <= this, assume it's decimal (0-1) not percentage
PRICE_TIMELINE_MAX_AGE_HOURS = 1  # Maximum age of cached price timeline in hours

# Tolerance Constants
POWER_ALLOCATION_TOLERANCE = 1.1  # 10% tolerance for power allocation validation
POWER_ALLOCATION_PRECISION = 1  # Watt precision for allocation mismatch detection
PEAK_THRESHOLD_MULTIPLIER = 1.05  # 5% over effective peak for peak detection

# Dynamic Threshold Constants (used in dynamic_threshold.py)
DYNAMIC_THRESHOLD_HIGH_VOLATILITY = 0.5  # >50% price range = high volatility
DYNAMIC_THRESHOLD_MEDIUM_VOLATILITY = 0.3  # 30-50% price range = medium volatility
DYNAMIC_THRESHOLD_HIGH_VOL_RANGE = 0.4  # Charge at bottom 40% when high volatility
DYNAMIC_THRESHOLD_MEDIUM_VOL_RANGE = 0.6  # Charge at bottom 60% when medium volatility
DYNAMIC_THRESHOLD_LOW_VOL_RANGE = 0.8  # Charge at bottom 80% when low volatility
DYNAMIC_THRESHOLD_NEXT_HOUR_IMPROVEMENT = 0.9  # 10% better next hour = wait
DYNAMIC_THRESHOLD_CONFIDENCE_REDUCTION = 0.3  # Reduce confidence to 30% if improving
DYNAMIC_THRESHOLD_WEIGHT_PRICE_QUALITY = 0.4  # Weight for price quality factor
DYNAMIC_THRESHOLD_WEIGHT_THRESHOLD = 0.4  # Weight for dynamic threshold factor
DYNAMIC_THRESHOLD_WEIGHT_NEXT_HOUR = 0.2  # Weight for next hour factor
DYNAMIC_THRESHOLD_MAX_CONFIDENCE_ABOVE = 0.25  # Max confidence when above threshold

# Battery Capacity Fallback
BATTERY_CAPACITY_FALLBACK_WEIGHT = 1.0  # Fallback weight when capacity not configured

# Average Threshold Calculation Constants
AVERAGE_THRESHOLD_HYSTERESIS_COUNT = 3  # Require N consecutive valid calculations
AVERAGE_THRESHOLD_DEFAULT_INTERVAL_SECONDS = 900  # 15 minutes default interval

# Update Throttling Constants
MIN_UPDATE_INTERVAL_SECONDS = 10  # Minimum seconds between entity-triggered updates

# LRU Cache Sizes
PRICE_POSITION_CACHE_SIZE = 32  # Reduced from 128 - typical daily usage is <10

SERVICE_SET_MANUAL_OVERRIDE = "set_manual_override"
SERVICE_CLEAR_MANUAL_OVERRIDE = "clear_manual_override"

ATTR_ENTRY_ID = "entry_id"
ATTR_TARGET = "target"
ATTR_ACTION = "action"
ATTR_DURATION = "duration"
ATTR_REASON = "reason"
ATTR_CHARGER_LIMIT_OVERRIDE = "charger_limit"
ATTR_GRID_SETPOINT_OVERRIDE = "grid_setpoint"

MANUAL_OVERRIDE_ACTION_FORCE_CHARGE = "force_charge"
MANUAL_OVERRIDE_ACTION_FORCE_WAIT = "force_wait"
MANUAL_OVERRIDE_TARGET_BATTERY = "battery"
MANUAL_OVERRIDE_TARGET_CAR = "car"
MANUAL_OVERRIDE_TARGET_BOTH = "both"
MANUAL_OVERRIDE_TARGET_CHARGER_LIMIT = "charger_limit"
MANUAL_OVERRIDE_TARGET_GRID_SETPOINT = "grid_setpoint"
MANUAL_OVERRIDE_TARGET_ALL = "all"

INTEGRATION_VERSION = "4.7.2"
