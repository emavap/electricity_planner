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
CONF_BATTERY_POWER_SENSORS = "battery_power_sensors"  # Per-phase battery power (W)
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
CONF_SOLAR_PEAK_EMERGENCY_SOC = "solar_peak_emergency_soc"
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
DEFAULT_SOLAR_PEAK_EMERGENCY_SOC = 25  # SOC below which to charge even during solar peak
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

# Algorithm Constants
DEFAULT_SIGNIFICANT_PRICE_DROP_THRESHOLD = 0.15  # 15% price drop threshold
DEFAULT_BASE_GRID_SETPOINT = 2500  # Conservative base grid limit (W)
DEFAULT_MONTHLY_PEAK_SAFETY_MARGIN = 0.9  # Use 90% of monthly peak
DEFAULT_CAR_PRIORITY_SOC_THRESHOLD = 70  # Above this SOC, car can use surplus
DEFAULT_CRITICAL_SOC_THRESHOLD = 30  # Below this is considered critical
DEFAULT_MEDIUM_SOC_THRESHOLD = 50  # Medium SOC charging threshold
DEFAULT_HIGH_SOC_THRESHOLD = 60  # High SOC threshold for time-based charging

# Cache and Performance Constants
NORDPOOL_CACHE_MAX_SIZE = 10  # Maximum number of cached Nord Pool price entries
NORDPOOL_CACHE_TTL_MINUTES = 5  # Cache time-to-live in minutes
PRICE_TIMELINE_MAX_AGE_HOURS = 1  # Maximum age of cached price timeline in hours
TRANSPORT_COST_CACHE_TTL_MINUTES = 30  # Transport cost lookup cache TTL

# Tolerance Constants
POWER_ALLOCATION_TOLERANCE = 1.1  # 10% tolerance for power allocation validation
POWER_ALLOCATION_PRECISION = 1  # Watt precision for allocation mismatch detection
PRICE_INTERVAL_GAP_TOLERANCE_SECONDS = 5  # Maximum gap between price intervals
PEAK_THRESHOLD_MULTIPLIER = 1.05  # 5% over effective peak for peak detection

ATTR_BATTERY_GRID_CHARGING = "battery_grid_charging"
ATTR_CAR_GRID_CHARGING = "car_grid_charging"
ATTR_BATTERY_REASON = "battery_reason"
ATTR_CAR_REASON = "car_reason"
ATTR_NEXT_EVALUATION = "next_evaluation"
ATTR_CURRENT_PRICE = "current_price"
ATTR_CHARGER_LIMIT = "charger_limit"
ATTR_GRID_SETPOINT = "grid_setpoint"
ATTR_FEEDIN_SOLAR = "feedin_solar"
ATTR_FEEDIN_REASON = "feedin_reason"

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

INTEGRATION_VERSION = "4.4.6"
