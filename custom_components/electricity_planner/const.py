"""Constants for the Electricity Planner integration."""

DOMAIN = "electricity_planner"

CONF_CURRENT_PRICE_ENTITY = "current_price_entity"
CONF_HIGHEST_PRICE_ENTITY = "highest_price_entity"
CONF_LOWEST_PRICE_ENTITY = "lowest_price_entity"
CONF_NEXT_PRICE_ENTITY = "next_price_entity"
CONF_BATTERY_SOC_ENTITIES = "battery_soc_entities"
CONF_SOLAR_SURPLUS_ENTITY = "solar_surplus_entity"
CONF_CAR_CHARGING_POWER_ENTITY = "car_charging_power_entity"
CONF_MONTHLY_GRID_PEAK_ENTITY = "monthly_grid_peak_entity"
CONF_WEATHER_ENTITY = "weather_entity"

CONF_MIN_SOC_THRESHOLD = "min_soc_threshold"
CONF_MAX_SOC_THRESHOLD = "max_soc_threshold"
CONF_PRICE_THRESHOLD = "price_threshold"
CONF_EMERGENCY_SOC_THRESHOLD = "emergency_soc_threshold"
CONF_VERY_LOW_PRICE_THRESHOLD = "very_low_price_threshold"
CONF_SIGNIFICANT_SOLAR_THRESHOLD = "significant_solar_threshold"
CONF_POOR_SOLAR_FORECAST_THRESHOLD = "poor_solar_forecast_threshold"
CONF_EXCELLENT_SOLAR_FORECAST_THRESHOLD = "excellent_solar_forecast_threshold"
CONF_FEEDIN_PRICE_THRESHOLD = "feedin_price_threshold"

DEFAULT_MIN_SOC = 20
DEFAULT_MAX_SOC = 90
DEFAULT_PRICE_THRESHOLD = 0.15
DEFAULT_EMERGENCY_SOC = 15
DEFAULT_VERY_LOW_PRICE_THRESHOLD = 30  # Bottom 30% of daily range
DEFAULT_SIGNIFICANT_SOLAR_THRESHOLD = 1000  # 1kW
DEFAULT_POOR_SOLAR_FORECAST = 40  # Below 40% = poor forecast
DEFAULT_EXCELLENT_SOLAR_FORECAST = 80  # Above 80% = excellent forecast
DEFAULT_FEEDIN_PRICE_THRESHOLD = 0.05  # €0.05/kWh - export only above this price

ATTR_BATTERY_GRID_CHARGING = "battery_grid_charging"
ATTR_CAR_GRID_CHARGING = "car_grid_charging"
ATTR_BATTERY_REASON = "battery_reason"
ATTR_CAR_REASON = "car_reason"
ATTR_NEXT_EVALUATION = "next_evaluation"
ATTR_CURRENT_PRICE = "current_price"
ATTR_SOLAR_FORECAST = "solar_forecast"
ATTR_CHARGER_LIMIT = "charger_limit"
ATTR_GRID_SETPOINT = "grid_setpoint"
ATTR_FEEDIN_SOLAR = "feedin_solar"
ATTR_FEEDIN_REASON = "feedin_reason"