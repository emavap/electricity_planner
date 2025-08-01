"""Constants for the Electricity Planner integration."""

DOMAIN = "electricity_planner"

CONF_CURRENT_PRICE_ENTITY = "current_price_entity"
CONF_HIGHEST_PRICE_ENTITY = "highest_price_entity"
CONF_LOWEST_PRICE_ENTITY = "lowest_price_entity"
CONF_NEXT_PRICE_ENTITY = "next_price_entity"
CONF_BATTERY_SOC_ENTITIES = "battery_soc_entities"
CONF_SOLAR_SURPLUS_ENTITY = "solar_surplus_entity"
CONF_CAR_CHARGING_POWER_ENTITY = "car_charging_power_entity"

CONF_MIN_SOC_THRESHOLD = "min_soc_threshold"
CONF_MAX_SOC_THRESHOLD = "max_soc_threshold"
CONF_PRICE_THRESHOLD = "price_threshold"

DEFAULT_MIN_SOC = 20
DEFAULT_MAX_SOC = 90
DEFAULT_PRICE_THRESHOLD = 0.15

ATTR_BATTERY_GRID_CHARGING = "battery_grid_charging"
ATTR_CAR_GRID_CHARGING = "car_grid_charging"
ATTR_BATTERY_REASON = "battery_reason"
ATTR_CAR_REASON = "car_reason"
ATTR_NEXT_EVALUATION = "next_evaluation"
ATTR_CURRENT_PRICE = "current_price"
ATTR_SOLAR_FORECAST = "solar_forecast"