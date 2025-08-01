"""Constants for the Electricity Planner integration."""

DOMAIN = "electricity_planner"

CONF_ELECTRICITY_PRICE_ENTITY = "electricity_price_entity"
CONF_BATTERY_SOC_ENTITIES = "battery_soc_entities"
CONF_BATTERY_CAPACITY_ENTITIES = "battery_capacity_entities"
CONF_SOLAR_FORECAST_ENTITY = "solar_forecast_entity"
CONF_SOLAR_PRODUCTION_ENTITY = "solar_production_entity"
CONF_CAR_CHARGER_ENTITY = "car_charger_entity"
CONF_GRID_POWER_ENTITY = "grid_power_entity"

CONF_MIN_SOC_THRESHOLD = "min_soc_threshold"
CONF_MAX_SOC_THRESHOLD = "max_soc_threshold"
CONF_PRICE_THRESHOLD = "price_threshold"
CONF_SOLAR_FORECAST_HOURS = "solar_forecast_hours"
CONF_CAR_CHARGING_HOURS = "car_charging_hours"

DEFAULT_MIN_SOC = 20
DEFAULT_MAX_SOC = 90
DEFAULT_PRICE_THRESHOLD = 0.15
DEFAULT_SOLAR_FORECAST_HOURS = 12
DEFAULT_CAR_CHARGING_HOURS = 8

ATTR_CHARGING_DECISION = "charging_decision"
ATTR_REASON = "reason"
ATTR_NEXT_EVALUATION = "next_evaluation"
ATTR_CURRENT_PRICE = "current_price"
ATTR_AVERAGE_PRICE = "average_price"
ATTR_SOLAR_FORECAST = "solar_forecast"