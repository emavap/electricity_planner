"""Config flow for Electricity Planner integration."""
from __future__ import annotations
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry

from .const import (
    DOMAIN,
    CONF_CURRENT_PRICE_ENTITY,
    CONF_HIGHEST_PRICE_ENTITY,
    CONF_LOWEST_PRICE_ENTITY,
    CONF_NEXT_PRICE_ENTITY,
    CONF_BATTERY_SOC_ENTITIES,
    CONF_BATTERY_CAPACITIES,
    CONF_SOLAR_PRODUCTION_ENTITY,
    CONF_HOUSE_CONSUMPTION_ENTITY,
    CONF_CAR_CHARGING_POWER_ENTITY,
    CONF_MONTHLY_GRID_PEAK_ENTITY,
    CONF_SOLAR_FORECAST_CURRENT_ENTITY,
    CONF_SOLAR_FORECAST_NEXT_ENTITY,
    CONF_SOLAR_FORECAST_TODAY_ENTITY,
    CONF_SOLAR_FORECAST_REMAINING_TODAY_ENTITY,
    CONF_SOLAR_FORECAST_TOMORROW_ENTITY,
    CONF_TRANSPORT_COST_ENTITY,
    CONF_MIN_SOC_THRESHOLD,
    CONF_MAX_SOC_THRESHOLD,
    CONF_PRICE_THRESHOLD,
    CONF_EMERGENCY_SOC_THRESHOLD,
    CONF_VERY_LOW_PRICE_THRESHOLD,
    CONF_SIGNIFICANT_SOLAR_THRESHOLD,
    CONF_POOR_SOLAR_FORECAST_THRESHOLD,
    CONF_EXCELLENT_SOLAR_FORECAST_THRESHOLD,
    CONF_FEEDIN_PRICE_THRESHOLD,
    CONF_MAX_BATTERY_POWER,
    CONF_MAX_CAR_POWER,
    CONF_MAX_GRID_POWER,
    CONF_MIN_CAR_CHARGING_THRESHOLD,
    CONF_SOLAR_PEAK_EMERGENCY_SOC,
    CONF_PREDICTIVE_CHARGING_MIN_SOC,
    CONF_BASE_GRID_SETPOINT,
    CONF_USE_DYNAMIC_THRESHOLD,
    CONF_DYNAMIC_THRESHOLD_CONFIDENCE,
    CONF_PRICE_ADJUSTMENT_MULTIPLIER,
    CONF_PRICE_ADJUSTMENT_OFFSET,
    CONF_FEEDIN_ADJUSTMENT_MULTIPLIER,
    CONF_FEEDIN_ADJUSTMENT_OFFSET,
    DEFAULT_MIN_SOC,
    DEFAULT_MAX_SOC,
    DEFAULT_PRICE_THRESHOLD,
    DEFAULT_EMERGENCY_SOC,
    DEFAULT_VERY_LOW_PRICE_THRESHOLD,
    DEFAULT_SIGNIFICANT_SOLAR_THRESHOLD,
    DEFAULT_POOR_SOLAR_FORECAST,
    DEFAULT_EXCELLENT_SOLAR_FORECAST,
    DEFAULT_FEEDIN_PRICE_THRESHOLD,
    DEFAULT_MAX_BATTERY_POWER,
    DEFAULT_MAX_CAR_POWER,
    DEFAULT_MAX_GRID_POWER,
    DEFAULT_MIN_CAR_CHARGING_THRESHOLD,
    DEFAULT_SOLAR_PEAK_EMERGENCY_SOC,
    DEFAULT_PREDICTIVE_CHARGING_MIN_SOC,
    DEFAULT_BASE_GRID_SETPOINT,
    DEFAULT_USE_DYNAMIC_THRESHOLD,
    DEFAULT_DYNAMIC_THRESHOLD_CONFIDENCE,
    DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER,
    DEFAULT_PRICE_ADJUSTMENT_OFFSET,
    DEFAULT_FEEDIN_ADJUSTMENT_MULTIPLIER,
    DEFAULT_FEEDIN_ADJUSTMENT_OFFSET,
)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Electricity Planner."""

    VERSION = 6

    def __init__(self):
        """Initialize the config flow."""
        self.data = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step - Entity Selection."""
        if user_input is not None:
            self.data.update(user_input)
            return await self.async_step_battery_capacities()


        schema = vol.Schema({
            vol.Required(CONF_CURRENT_PRICE_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(CONF_HIGHEST_PRICE_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(CONF_LOWEST_PRICE_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(CONF_NEXT_PRICE_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(CONF_BATTERY_SOC_ENTITIES): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="sensor",
                    multiple=True
                )
            ),
            vol.Required(CONF_SOLAR_PRODUCTION_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(CONF_HOUSE_CONSUMPTION_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_CAR_CHARGING_POWER_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_MONTHLY_GRID_PEAK_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_SOLAR_FORECAST_CURRENT_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_SOLAR_FORECAST_NEXT_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_SOLAR_FORECAST_TODAY_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_SOLAR_FORECAST_REMAINING_TODAY_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_SOLAR_FORECAST_TOMORROW_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_TRANSPORT_COST_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
        })

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            description_placeholders={
                "current_price": "Current electricity price from Nord Pool",
                "highest_price": "Highest price today from Nord Pool",
                "lowest_price": "Lowest price today from Nord Pool",
                "next_price": "Next hour price from Nord Pool",
                "battery_soc": "Battery State of Charge entities",
                "solar_production": "Current solar production in W",
                "house_consumption": "Current house power consumption in W", 
                "car_charging": "Car charging power in W (optional)",
                "monthly_grid_peak": "Current month grid peak in W (optional)",
                "solar_forecast_current": "Solar forecast for current hour (kWh) (optional)",
                "solar_forecast_next": "Solar forecast for next hour (kWh) (optional)", 
                "solar_forecast_today": "Total solar forecast for today (kWh) (optional)",
                "solar_forecast_remaining": "Remaining solar forecast for today (kWh) (optional)",
                "solar_forecast_tomorrow": "Solar forecast for tomorrow (kWh) (optional)",
                "transport_cost": "Optional transport cost sensor (€/kWh) added to buy price",
            },
        )

    async def async_step_battery_capacities(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the battery capacities step - Configure capacity for each battery."""
        if user_input is not None:
            battery_entities = self.data.get(CONF_BATTERY_SOC_ENTITIES, [])
            battery_capacities = {}

            for entity_id in battery_entities:
                entity_key = f"capacity_{entity_id.replace('.', '_')}"
                if entity_key in user_input:
                    battery_capacities[entity_id] = user_input[entity_key]

            self.data[CONF_BATTERY_CAPACITIES] = battery_capacities
            return await self.async_step_settings()

        battery_entities = self.data.get(CONF_BATTERY_SOC_ENTITIES, [])

        if not battery_entities:
            self.data[CONF_BATTERY_CAPACITIES] = {}
            return await self.async_step_settings()

        battery_capacities = {}
        for entity_id in battery_entities:
            entity_key = f"capacity_{entity_id.replace('.', '_')}"
            battery_capacities[vol.Optional(entity_key, default=10.0)] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1.0,
                    max=200.0,
                    step=0.5,
                    unit_of_measurement="kWh",
                    mode=selector.NumberSelectorMode.BOX
                )
            )

        if not battery_capacities:
            self.data[CONF_BATTERY_CAPACITIES] = {}
            return await self.async_step_settings()

        schema = vol.Schema(battery_capacities)

        entity_registry = async_get_entity_registry(self.hass)
        description_placeholders = {}

        for entity_id in battery_entities:
            entity_entry = entity_registry.async_get(entity_id)
            friendly_name = entity_entry.name if entity_entry and entity_entry.name else entity_id.split(".")[-1]
            entity_key = f"capacity_{entity_id.replace('.', '_')}"
            description_placeholders[entity_key] = f"Nominal capacity for {friendly_name}"

        return self.async_show_form(
            step_id="battery_capacities",
            data_schema=schema,
            description_placeholders=description_placeholders,
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the settings step - Thresholds and Preferences."""
        if user_input is not None:
            self.data.update(user_input)
            return await self.async_step_safety_limits()

        schema = vol.Schema({
            vol.Optional(
                CONF_MIN_SOC_THRESHOLD,
                default=DEFAULT_MIN_SOC
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=100, unit_of_measurement="%"
                )
            ),
            vol.Optional(
                CONF_MAX_SOC_THRESHOLD,
                default=DEFAULT_MAX_SOC
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=100, unit_of_measurement="%"
                )
            ),
            vol.Optional(
                CONF_PRICE_THRESHOLD,
                default=DEFAULT_PRICE_THRESHOLD
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=1, step=0.01, unit_of_measurement="€/kWh"
                )
            ),
            vol.Optional(
                CONF_PRICE_ADJUSTMENT_MULTIPLIER,
                default=DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=5, step=0.01
                )
            ),
            vol.Optional(
                CONF_PRICE_ADJUSTMENT_OFFSET,
                default=DEFAULT_PRICE_ADJUSTMENT_OFFSET
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=-0.5, max=0.5, step=0.001, unit_of_measurement="€/kWh"
                )
            ),
            vol.Optional(
                CONF_EMERGENCY_SOC_THRESHOLD,
                default=DEFAULT_EMERGENCY_SOC
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=5, max=50, unit_of_measurement="%"
                )
            ),
            vol.Optional(
                CONF_VERY_LOW_PRICE_THRESHOLD,
                default=DEFAULT_VERY_LOW_PRICE_THRESHOLD
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=10, max=50, unit_of_measurement="%"
                )
            ),
            vol.Optional(
                CONF_SIGNIFICANT_SOLAR_THRESHOLD,
                default=DEFAULT_SIGNIFICANT_SOLAR_THRESHOLD
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=500, max=5000, step=100, unit_of_measurement="W"
                )
            ),
            vol.Optional(
                CONF_POOR_SOLAR_FORECAST_THRESHOLD,
                default=DEFAULT_POOR_SOLAR_FORECAST
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=10, max=60, unit_of_measurement="%"
                )
            ),
            vol.Optional(
                CONF_EXCELLENT_SOLAR_FORECAST_THRESHOLD,
                default=DEFAULT_EXCELLENT_SOLAR_FORECAST
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=60, max=95, unit_of_measurement="%"
                )
            ),
            vol.Optional(
                CONF_FEEDIN_PRICE_THRESHOLD,
                default=DEFAULT_FEEDIN_PRICE_THRESHOLD
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.0, max=1.0, step=0.01, unit_of_measurement="€/kWh"
                )
            ),
            vol.Optional(
                CONF_FEEDIN_ADJUSTMENT_MULTIPLIER,
                default=DEFAULT_FEEDIN_ADJUSTMENT_MULTIPLIER
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=-1, max=5, step=0.01
                )
            ),
            vol.Optional(
                CONF_FEEDIN_ADJUSTMENT_OFFSET,
                default=DEFAULT_FEEDIN_ADJUSTMENT_OFFSET
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=-0.5, max=0.5, step=0.001, unit_of_measurement="€/kWh"
                )
            ),
            vol.Optional(
                CONF_USE_DYNAMIC_THRESHOLD,
                default=DEFAULT_USE_DYNAMIC_THRESHOLD
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_DYNAMIC_THRESHOLD_CONFIDENCE,
                default=DEFAULT_DYNAMIC_THRESHOLD_CONFIDENCE
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=30, max=90, step=5, unit_of_measurement="%"
                )
            ),
        })

        return self.async_show_form(
            step_id="settings",
            data_schema=schema,
            description_placeholders={
                "min_soc": "Minimum battery charge level to maintain",
                "max_soc": "Maximum battery charge level target",
                "price_threshold": "Price threshold for charging decisions (maximum ceiling)",
                "price_adjustment_multiplier": "Multiplier applied to raw price (set 1.12 for the Belgian example)",
                "price_adjustment_offset": "Fixed €/kWh offset added after multiplier (e.g. 0.008 for 0.8 c€)",
                "emergency_soc": "Emergency SOC level that triggers charging regardless of price",
                "very_low_price": "Percentage threshold for 'very low' price in daily range",
                "significant_solar": "Solar surplus threshold considered significant",
                "poor_forecast": "Solar forecast below this percentage is considered poor",
                "excellent_forecast": "Solar forecast above this percentage is considered excellent",
                "use_dynamic_threshold": "Enable intelligent price analysis (more selective, better prices)",
                "dynamic_threshold_confidence": "Confidence required for dynamic charging (higher = more selective)",
                "feedin_price_threshold": "Minimum export price required when no adjustment is set",
                "feedin_adjustment_multiplier": "Multiplier applied to raw price when calculating net feed-in value",
                "feedin_adjustment_offset": "Fixed €/kWh offset added to feed-in price (negative values model costs)",
            },
        )

    async def async_step_safety_limits(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the safety limits step - Power and SOC Safety Limits."""
        if user_input is not None:
            self.data.update(user_input)
            return self.async_create_entry(
                title="Electricity Planner",
                data=self.data
            )

        schema = vol.Schema({
            vol.Optional(
                CONF_MAX_BATTERY_POWER,
                default=DEFAULT_MAX_BATTERY_POWER
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1000, max=10000, step=500, unit_of_measurement="W"
                )
            ),
            vol.Optional(
                CONF_MAX_CAR_POWER,
                default=DEFAULT_MAX_CAR_POWER
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1000, max=22000, step=1000, unit_of_measurement="W"
                )
            ),
            vol.Optional(
                CONF_MAX_GRID_POWER,
                default=DEFAULT_MAX_GRID_POWER
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=3000, max=30000, step=1000, unit_of_measurement="W"
                )
            ),
            vol.Optional(
                CONF_MIN_CAR_CHARGING_THRESHOLD,
                default=DEFAULT_MIN_CAR_CHARGING_THRESHOLD
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=50, max=500, step=50, unit_of_measurement="W"
                )
            ),
            vol.Optional(
                CONF_SOLAR_PEAK_EMERGENCY_SOC,
                default=DEFAULT_SOLAR_PEAK_EMERGENCY_SOC
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=15, max=40, unit_of_measurement="%"
                )
            ),
            vol.Optional(
                CONF_PREDICTIVE_CHARGING_MIN_SOC,
                default=DEFAULT_PREDICTIVE_CHARGING_MIN_SOC
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=20, max=60, unit_of_measurement="%"
                )
            ),
            vol.Optional(
                CONF_BASE_GRID_SETPOINT,
                default=DEFAULT_BASE_GRID_SETPOINT
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1000, max=10000, step=100, unit_of_measurement="W"
                )
            ),
        })

        return self.async_show_form(
            step_id="safety_limits",
            data_schema=schema,
            description_placeholders={
                "max_battery_power": "Maximum power limit for battery charging/discharging",
                "max_car_power": "Maximum power limit for car charging",
                "max_grid_power": "Maximum power limit from grid (safety limit)",
                "min_car_charging_threshold": "Minimum power to consider car 'charging'",
                "solar_peak_emergency_soc": "SOC below which to charge even during solar peak",
                "predictive_charging_min_soc": "Minimum SOC for predictive charging logic",
                "base_grid_setpoint": "Base minimum grid setpoint when no monthly peak data available",
            },
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        """Return the options flow."""
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Electricity Planner."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            updated_data = dict(self.config_entry.data)

            battery_entities = user_input.get(CONF_BATTERY_SOC_ENTITIES, [])
            battery_capacities = {}

            for entity_id in battery_entities:
                entity_key = f"capacity_{entity_id.replace('.', '_')}"
                if entity_key in user_input:
                    battery_capacities[entity_id] = user_input[entity_key]
                    user_input.pop(entity_key, None)

            user_input[CONF_BATTERY_CAPACITIES] = battery_capacities
            updated_data.update(user_input)

            self.hass.config_entries.async_update_entry(
                self.config_entry, data=updated_data
            )
            return self.async_create_entry(title="", data={})

        current_config = self.config_entry.data

        battery_entities = current_config.get(CONF_BATTERY_SOC_ENTITIES, [])
        current_capacities = current_config.get(CONF_BATTERY_CAPACITIES, {})

        schema_dict = {
            vol.Required(
                CONF_BATTERY_SOC_ENTITIES,
                default=battery_entities
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="sensor",
                    multiple=True
                )
            ),
        }

        for entity_id in battery_entities:
            entity_key = f"capacity_{entity_id.replace('.', '_')}"
            default_capacity = current_capacities.get(entity_id, 10.0)
            schema_dict[vol.Optional(entity_key, default=default_capacity)] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1.0,
                    max=200.0,
                    step=0.5,
                    unit_of_measurement="kWh",
                    mode=selector.NumberSelectorMode.BOX
                )
            )

        schema_dict.update({
            vol.Required(
                CONF_SOLAR_PRODUCTION_ENTITY,
                default=current_config.get(CONF_SOLAR_PRODUCTION_ENTITY)
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(
                CONF_HOUSE_CONSUMPTION_ENTITY,
                default=current_config.get(CONF_HOUSE_CONSUMPTION_ENTITY)
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(
                CONF_CAR_CHARGING_POWER_ENTITY,
                default=current_config.get(CONF_CAR_CHARGING_POWER_ENTITY)
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(
                CONF_MONTHLY_GRID_PEAK_ENTITY,
                default=current_config.get(CONF_MONTHLY_GRID_PEAK_ENTITY)
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(
                CONF_SOLAR_FORECAST_CURRENT_ENTITY,
                default=current_config.get(CONF_SOLAR_FORECAST_CURRENT_ENTITY)
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(
                CONF_SOLAR_FORECAST_NEXT_ENTITY,
                default=current_config.get(CONF_SOLAR_FORECAST_NEXT_ENTITY)
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(
                CONF_SOLAR_FORECAST_TODAY_ENTITY,
                default=current_config.get(CONF_SOLAR_FORECAST_TODAY_ENTITY)
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(
                CONF_SOLAR_FORECAST_REMAINING_TODAY_ENTITY,
                default=current_config.get(CONF_SOLAR_FORECAST_REMAINING_TODAY_ENTITY)
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(
                CONF_SOLAR_FORECAST_TOMORROW_ENTITY,
                default=current_config.get(CONF_SOLAR_FORECAST_TOMORROW_ENTITY)
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(
                CONF_TRANSPORT_COST_ENTITY,
                default=current_config.get(CONF_TRANSPORT_COST_ENTITY)
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(
                CONF_PRICE_ADJUSTMENT_MULTIPLIER,
                default=current_config.get(
                    CONF_PRICE_ADJUSTMENT_MULTIPLIER, DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER
                )
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=5, step=0.01
                )
            ),
            vol.Optional(
                CONF_PRICE_ADJUSTMENT_OFFSET,
                default=current_config.get(
                    CONF_PRICE_ADJUSTMENT_OFFSET, DEFAULT_PRICE_ADJUSTMENT_OFFSET
                )
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=-0.5, max=0.5, step=0.001, unit_of_measurement="€/kWh"
                )
            ),
            vol.Optional(
                CONF_FEEDIN_PRICE_THRESHOLD,
                default=current_config.get(CONF_FEEDIN_PRICE_THRESHOLD, DEFAULT_FEEDIN_PRICE_THRESHOLD)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.0, max=1.0, step=0.01, unit_of_measurement="€/kWh"
                )
            ),
            vol.Optional(
                CONF_FEEDIN_ADJUSTMENT_MULTIPLIER,
                default=current_config.get(
                    CONF_FEEDIN_ADJUSTMENT_MULTIPLIER, DEFAULT_FEEDIN_ADJUSTMENT_MULTIPLIER
                )
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=-1, max=5, step=0.01
                )
            ),
            vol.Optional(
                CONF_FEEDIN_ADJUSTMENT_OFFSET,
                default=current_config.get(
                    CONF_FEEDIN_ADJUSTMENT_OFFSET, DEFAULT_FEEDIN_ADJUSTMENT_OFFSET
                )
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=-0.5, max=0.5, step=0.001, unit_of_measurement="€/kWh"
                )
            ),
            vol.Optional(
                CONF_MIN_SOC_THRESHOLD,
                default=current_config.get(CONF_MIN_SOC_THRESHOLD, DEFAULT_MIN_SOC)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=100, unit_of_measurement="%"
                )
            ),
            vol.Optional(
                CONF_MAX_SOC_THRESHOLD,
                default=current_config.get(CONF_MAX_SOC_THRESHOLD, DEFAULT_MAX_SOC)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=100, unit_of_measurement="%"
                )
            ),
            vol.Optional(
                CONF_PRICE_THRESHOLD,
                default=current_config.get(CONF_PRICE_THRESHOLD, DEFAULT_PRICE_THRESHOLD)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=1, step=0.01, unit_of_measurement="€/kWh"
                )
            ),
            vol.Optional(
                CONF_EMERGENCY_SOC_THRESHOLD,
                default=current_config.get(CONF_EMERGENCY_SOC_THRESHOLD, DEFAULT_EMERGENCY_SOC)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=5, max=50, unit_of_measurement="%"
                )
            ),
            vol.Optional(
                CONF_VERY_LOW_PRICE_THRESHOLD,
                default=current_config.get(CONF_VERY_LOW_PRICE_THRESHOLD, DEFAULT_VERY_LOW_PRICE_THRESHOLD)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=10, max=50, unit_of_measurement="%"
                )
            ),
            vol.Optional(
                CONF_SIGNIFICANT_SOLAR_THRESHOLD,
                default=current_config.get(CONF_SIGNIFICANT_SOLAR_THRESHOLD, DEFAULT_SIGNIFICANT_SOLAR_THRESHOLD)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=500, max=5000, step=100, unit_of_measurement="W"
                )
            ),
            vol.Optional(
                CONF_POOR_SOLAR_FORECAST_THRESHOLD,
                default=current_config.get(CONF_POOR_SOLAR_FORECAST_THRESHOLD, DEFAULT_POOR_SOLAR_FORECAST)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=10, max=60, unit_of_measurement="%"
                )
            ),
            vol.Optional(
                CONF_EXCELLENT_SOLAR_FORECAST_THRESHOLD,
                default=current_config.get(CONF_EXCELLENT_SOLAR_FORECAST_THRESHOLD, DEFAULT_EXCELLENT_SOLAR_FORECAST)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=60, max=95, unit_of_measurement="%"
                )
            ),
            vol.Optional(
                CONF_FEEDIN_PRICE_THRESHOLD,
                default=current_config.get(CONF_FEEDIN_PRICE_THRESHOLD, DEFAULT_FEEDIN_PRICE_THRESHOLD)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.0, max=1.0, step=0.01, unit_of_measurement="€/kWh"
                )
            ),
            vol.Optional(
                CONF_USE_DYNAMIC_THRESHOLD,
                default=current_config.get(CONF_USE_DYNAMIC_THRESHOLD, DEFAULT_USE_DYNAMIC_THRESHOLD)
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_DYNAMIC_THRESHOLD_CONFIDENCE,
                default=current_config.get(CONF_DYNAMIC_THRESHOLD_CONFIDENCE, DEFAULT_DYNAMIC_THRESHOLD_CONFIDENCE)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=30, max=90, step=5, unit_of_measurement="%"
                )
            ),
            vol.Optional(
                CONF_MAX_BATTERY_POWER,
                default=current_config.get(CONF_MAX_BATTERY_POWER, DEFAULT_MAX_BATTERY_POWER)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1000, max=10000, step=500, unit_of_measurement="W"
                )
            ),
            vol.Optional(
                CONF_MAX_CAR_POWER,
                default=current_config.get(CONF_MAX_CAR_POWER, DEFAULT_MAX_CAR_POWER)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1000, max=22000, step=1000, unit_of_measurement="W"
                )
            ),
            vol.Optional(
                CONF_MAX_GRID_POWER,
                default=current_config.get(CONF_MAX_GRID_POWER, DEFAULT_MAX_GRID_POWER)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=3000, max=30000, step=1000, unit_of_measurement="W"
                )
            ),
            vol.Optional(
                CONF_MIN_CAR_CHARGING_THRESHOLD,
                default=current_config.get(CONF_MIN_CAR_CHARGING_THRESHOLD, DEFAULT_MIN_CAR_CHARGING_THRESHOLD)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=50, max=500, step=50, unit_of_measurement="W"
                )
            ),
            vol.Optional(
                CONF_SOLAR_PEAK_EMERGENCY_SOC,
                default=current_config.get(CONF_SOLAR_PEAK_EMERGENCY_SOC, DEFAULT_SOLAR_PEAK_EMERGENCY_SOC)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=15, max=40, unit_of_measurement="%"
                )
            ),
            vol.Optional(
                CONF_PREDICTIVE_CHARGING_MIN_SOC,
                default=current_config.get(CONF_PREDICTIVE_CHARGING_MIN_SOC, DEFAULT_PREDICTIVE_CHARGING_MIN_SOC)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=20, max=60, unit_of_measurement="%"
                )
            ),
            vol.Optional(
                CONF_BASE_GRID_SETPOINT,
                default=current_config.get(CONF_BASE_GRID_SETPOINT, DEFAULT_BASE_GRID_SETPOINT)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1000, max=10000, step=100, unit_of_measurement="W"
                )
            ),
        })

        schema = vol.Schema(schema_dict)
        return self.async_show_form(step_id="init", data_schema=schema)
