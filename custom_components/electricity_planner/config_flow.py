"""Config flow for Electricity Planner integration."""
from __future__ import annotations

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
    CONF_SOLAR_SURPLUS_ENTITY,
    CONF_CAR_CHARGING_POWER_ENTITY,
    CONF_MIN_SOC_THRESHOLD,
    CONF_MAX_SOC_THRESHOLD,
    CONF_PRICE_THRESHOLD,
    DEFAULT_MIN_SOC,
    DEFAULT_MAX_SOC,
    DEFAULT_PRICE_THRESHOLD,
)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Electricity Planner."""

    VERSION = 1

    def __init__(self):
        """Initialize the config flow."""
        self.data = {}

    async def async_step_user(
        self, user_input: dict[str, any] | None = None
    ) -> FlowResult:
        """Handle the initial step - Entity Selection."""
        if user_input is not None:
            self.data.update(user_input)
            return await self.async_step_settings()


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
            vol.Required(CONF_SOLAR_SURPLUS_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(CONF_CAR_CHARGING_POWER_ENTITY): selector.EntitySelector(
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
                "solar_surplus": "Current solar surplus (production - consumption) in W",
                "car_charging": "Car charging power in W (optional)",
            },
        )

    async def async_step_settings(
        self, user_input: dict[str, any] | None = None
    ) -> FlowResult:
        """Handle the settings step - Thresholds and Preferences."""
        if user_input is not None:
            self.data.update(user_input)
            return self.async_create_entry(
                title="Electricity Planner",
                data=self.data
            )

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
        })

        return self.async_show_form(
            step_id="settings",
            data_schema=schema,
            description_placeholders={
                "min_soc": "Minimum battery charge level to maintain",
                "max_soc": "Maximum battery charge level target", 
                "price_threshold": "Price threshold for charging decisions",
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
        self, user_input: dict[str, any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            # Merge with existing entity configuration
            updated_data = dict(self.config_entry.data)
            updated_data.update(user_input)
            self.hass.config_entries.async_update_entry(
                self.config_entry, data=updated_data
            )
            return self.async_create_entry(title="", data={})

        current_config = self.config_entry.data

        schema = vol.Schema({
            vol.Required(
                CONF_BATTERY_SOC_ENTITIES,
                default=current_config.get(CONF_BATTERY_SOC_ENTITIES, [])
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(
                    domain="sensor",
                    multiple=True
                )
            ),
            vol.Required(
                CONF_SOLAR_SURPLUS_ENTITY,
                default=current_config.get(CONF_SOLAR_SURPLUS_ENTITY)
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
        })

        return self.async_show_form(step_id="init", data_schema=schema)