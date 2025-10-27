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
    CONF_PHASE_MODE,
    PHASE_MODE_SINGLE,
    PHASE_MODE_THREE,
    CONF_PHASES,
    PHASE_IDS,
    DEFAULT_PHASE_NAMES,
    CONF_PHASE_NAME,
    CONF_PHASE_SOLAR_ENTITY,
    CONF_PHASE_CONSUMPTION_ENTITY,
    CONF_PHASE_CAR_ENTITY,
    CONF_PHASE_BATTERY_POWER_ENTITY,
    CONF_NORDPOOL_CONFIG_ENTRY,
    CONF_CURRENT_PRICE_ENTITY,
    CONF_HIGHEST_PRICE_ENTITY,
    CONF_LOWEST_PRICE_ENTITY,
    CONF_NEXT_PRICE_ENTITY,
    CONF_BATTERY_SOC_ENTITIES,
    CONF_BATTERY_CAPACITIES,
    CONF_BATTERY_PHASE_ASSIGNMENTS,
    CONF_SOLAR_PRODUCTION_ENTITY,
    CONF_HOUSE_CONSUMPTION_ENTITY,
    CONF_CAR_CHARGING_POWER_ENTITY,
    CONF_MONTHLY_GRID_PEAK_ENTITY,
    CONF_TRANSPORT_COST_ENTITY,
    CONF_GRID_POWER_ENTITY,
    CONF_MIN_SOC_THRESHOLD,
    CONF_MAX_SOC_THRESHOLD,
    CONF_PRICE_THRESHOLD,
    CONF_EMERGENCY_SOC_THRESHOLD,
    CONF_VERY_LOW_PRICE_THRESHOLD,
    CONF_SIGNIFICANT_SOLAR_THRESHOLD,
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
    CONF_USE_AVERAGE_THRESHOLD,
    CONF_MIN_CAR_CHARGING_DURATION,
    CONF_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
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
    DEFAULT_USE_AVERAGE_THRESHOLD,
    DEFAULT_MIN_CAR_CHARGING_DURATION,
    DEFAULT_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
    DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER,
    DEFAULT_PRICE_ADJUSTMENT_OFFSET,
    DEFAULT_FEEDIN_ADJUSTMENT_MULTIPLIER,
    DEFAULT_FEEDIN_ADJUSTMENT_OFFSET,
)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Electricity Planner."""

    VERSION = 10

    def __init__(self):
        """Initialize the config flow."""
        self.data = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step - topology selection."""
        if user_input is not None:
            phase_mode = user_input.get(CONF_PHASE_MODE, PHASE_MODE_SINGLE)
            self.data[CONF_PHASE_MODE] = phase_mode
            return await self.async_step_entities()

        default_mode = self.data.get(CONF_PHASE_MODE, PHASE_MODE_SINGLE)
        schema = vol.Schema(
            {
                vol.Required(CONF_PHASE_MODE, default=default_mode): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {
                                "label": "Single-phase system",
                                "value": PHASE_MODE_SINGLE,
                            },
                            {
                                "label": "Three-phase system (L1/L2/L3)",
                                "value": PHASE_MODE_THREE,
                            },
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            description_placeholders={
                "phase_mode": "Select single-phase or three-phase operation. Three-phase enables per-phase inputs."
            },
        )

    async def async_step_entities(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Collect entity configuration based on phase topology."""
        phase_mode: str = self.data.get(CONF_PHASE_MODE, PHASE_MODE_SINGLE)
        existing_phases: dict[str, Any] = self.data.get(CONF_PHASES, {})

        if user_input is not None:
            processed_input = dict(user_input)

            if phase_mode == PHASE_MODE_THREE:
                phases_config: dict[str, dict[str, Any]] = {}

                for phase_id in PHASE_IDS:
                    solar_key = f"{phase_id}_{CONF_PHASE_SOLAR_ENTITY}"
                    consumption_key = f"{phase_id}_{CONF_PHASE_CONSUMPTION_ENTITY}"
                    car_key = f"{phase_id}_{CONF_PHASE_CAR_ENTITY}"
                    battery_power_key = f"{phase_id}_{CONF_PHASE_BATTERY_POWER_ENTITY}"

                    solar_entity = processed_input.pop(solar_key, None)
                    consumption_entity = processed_input.pop(consumption_key, None)
                    car_entity = processed_input.pop(car_key, None)
                    battery_power_entity = processed_input.pop(battery_power_key, None)

                    existing_phase = existing_phases.get(phase_id, {})

                    # Only create phase entry if at least one sensor is provided or phase already exists
                    # This prevents creating "ghost" phase configs with all None values
                    if (
                        solar_entity is not None
                        or consumption_entity is not None
                        or car_entity is not None
                        or battery_power_entity is not None
                        or existing_phase
                    ):
                        phase_entry = {
                            CONF_PHASE_NAME: existing_phase.get(
                                CONF_PHASE_NAME, DEFAULT_PHASE_NAMES[phase_id]
                            ),
                            CONF_PHASE_SOLAR_ENTITY: solar_entity
                            if solar_entity is not None
                            else existing_phase.get(CONF_PHASE_SOLAR_ENTITY),
                            CONF_PHASE_CONSUMPTION_ENTITY: consumption_entity
                            if consumption_entity is not None
                            else existing_phase.get(CONF_PHASE_CONSUMPTION_ENTITY),
                        }
                        if car_entity or existing_phase.get(CONF_PHASE_CAR_ENTITY):
                            phase_entry[CONF_PHASE_CAR_ENTITY] = (
                                car_entity if car_entity is not None
                                else existing_phase.get(CONF_PHASE_CAR_ENTITY)
                            )
                        if battery_power_entity or existing_phase.get(CONF_PHASE_BATTERY_POWER_ENTITY):
                            phase_entry[CONF_PHASE_BATTERY_POWER_ENTITY] = (
                                battery_power_entity if battery_power_entity is not None
                                else existing_phase.get(CONF_PHASE_BATTERY_POWER_ENTITY)
                            )
                        phases_config[phase_id] = phase_entry

                self.data[CONF_PHASES] = phases_config
                # Remove legacy single-phase bindings to avoid stale config
                for legacy_key in (
                    CONF_SOLAR_PRODUCTION_ENTITY,
                    CONF_HOUSE_CONSUMPTION_ENTITY,
                    CONF_CAR_CHARGING_POWER_ENTITY,
                ):
                    self.data.pop(legacy_key, None)
            else:
                # Ensure phase-specific configuration is cleared when returning to single-phase mode
                self.data.pop(CONF_PHASES, None)
                self.data.pop(CONF_BATTERY_PHASE_ASSIGNMENTS, None)

            self.data.update(processed_input)
            return await self.async_step_battery_capacities()

        schema_dict: dict[Any, Any] = {
            vol.Optional(
                CONF_NORDPOOL_CONFIG_ENTRY,
                default=self.data.get(CONF_NORDPOOL_CONFIG_ENTRY),
            ): selector.ConfigEntrySelector(
                selector.ConfigEntrySelectorConfig(integration="nordpool")
            ),
            vol.Required(
                CONF_CURRENT_PRICE_ENTITY,
                default=self.data.get(CONF_CURRENT_PRICE_ENTITY),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(
                CONF_HIGHEST_PRICE_ENTITY,
                default=self.data.get(CONF_HIGHEST_PRICE_ENTITY),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(
                CONF_LOWEST_PRICE_ENTITY,
                default=self.data.get(CONF_LOWEST_PRICE_ENTITY),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(
                CONF_NEXT_PRICE_ENTITY,
                default=self.data.get(CONF_NEXT_PRICE_ENTITY),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(
                CONF_BATTERY_SOC_ENTITIES,
                default=self.data.get(CONF_BATTERY_SOC_ENTITIES, []),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", multiple=True)
            ),
        }

        if phase_mode == PHASE_MODE_SINGLE:
            schema_dict.update(
                {
                    vol.Required(
                        CONF_SOLAR_PRODUCTION_ENTITY,
                        default=self.data.get(CONF_SOLAR_PRODUCTION_ENTITY),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Required(
                        CONF_HOUSE_CONSUMPTION_ENTITY,
                        default=self.data.get(CONF_HOUSE_CONSUMPTION_ENTITY),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Optional(
                        CONF_CAR_CHARGING_POWER_ENTITY,
                        default=self.data.get(CONF_CAR_CHARGING_POWER_ENTITY),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                }
            )
        else:
            phases_config = self.data.get(CONF_PHASES, {})
            for phase_id in PHASE_IDS:
                existing = phases_config.get(phase_id, {})
                solar_key = f"{phase_id}_{CONF_PHASE_SOLAR_ENTITY}"
                consumption_key = f"{phase_id}_{CONF_PHASE_CONSUMPTION_ENTITY}"
                car_key = f"{phase_id}_{CONF_PHASE_CAR_ENTITY}"
                battery_power_key = f"{phase_id}_{CONF_PHASE_BATTERY_POWER_ENTITY}"

                schema_dict[
                    vol.Optional(
                        solar_key,
                        default=existing.get(CONF_PHASE_SOLAR_ENTITY),
                    )
                ] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                )
                schema_dict[
                    vol.Optional(
                        consumption_key,
                        default=existing.get(CONF_PHASE_CONSUMPTION_ENTITY),
                    )
                ] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                )
                schema_dict[
                    vol.Optional(
                        car_key,
                        default=existing.get(CONF_PHASE_CAR_ENTITY),
                    )
                ] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                )
                schema_dict[
                    vol.Optional(
                        battery_power_key,
                        default=existing.get(CONF_PHASE_BATTERY_POWER_ENTITY),
                    )
                ] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                )

        schema_dict.update(
            {
                vol.Optional(
                    CONF_MONTHLY_GRID_PEAK_ENTITY,
                    default=self.data.get(CONF_MONTHLY_GRID_PEAK_ENTITY),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(
                    CONF_TRANSPORT_COST_ENTITY,
                    default=self.data.get(CONF_TRANSPORT_COST_ENTITY),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(
                    CONF_GRID_POWER_ENTITY,
                    default=self.data.get(CONF_GRID_POWER_ENTITY),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
            }
        )

        schema = vol.Schema(schema_dict)

        description_placeholders = {
            "nordpool_config_entry": "Nord Pool config entry (optional, enables full day price data)",
            "current_price": "Current electricity price from Nord Pool",
            "highest_price": "Highest price today from Nord Pool",
            "lowest_price": "Lowest price today from Nord Pool",
            "next_price": "Next hour price from Nord Pool",
            "battery_soc": "Battery State of Charge entities",
            "monthly_grid_peak": "Current month grid peak in W (optional)",
            "transport_cost": "Optional transport cost sensor (€/kWh) added to buy price",
        }
        if phase_mode == PHASE_MODE_SINGLE:
            description_placeholders.update(
                {
                    "solar_production": "Current solar production in W",
                    "house_consumption": "Current house power consumption in W",
                    "car_charging": "Car charging power in W (optional)",
                }
            )
        else:
            description_placeholders.update(
                {
                    "phase_inputs": "Provide solar, consumption, car, and battery power sensors for each phase (all optional)",
                }
            )

        return self.async_show_form(
            step_id="entities",
            data_schema=schema,
            description_placeholders=description_placeholders,
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
            if (
                self.data.get(CONF_PHASE_MODE) == PHASE_MODE_THREE
                and battery_entities
            ):
                return await self.async_step_battery_phase_assignment()
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

    async def async_step_battery_phase_assignment(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Assign batteries to phases for three-phase operation."""
        if self.data.get(CONF_PHASE_MODE) != PHASE_MODE_THREE:
            return await self.async_step_settings()

        battery_entities = self.data.get(CONF_BATTERY_SOC_ENTITIES, [])
        if not battery_entities:
            self.data[CONF_BATTERY_PHASE_ASSIGNMENTS] = {}
            return await self.async_step_settings()

        existing_assignments = self.data.get(CONF_BATTERY_PHASE_ASSIGNMENTS, {})

        if user_input is not None:
            assignments: dict[str, list[str]] = {}
            for entity_id in battery_entities:
                key = f"phase_assignment_{entity_id.replace('.', '_')}"
                selected = user_input.get(key) or existing_assignments.get(
                    entity_id
                )
                if not selected:
                    selected = [PHASE_IDS[0]]
                assignments[entity_id] = list(selected)

            self.data[CONF_BATTERY_PHASE_ASSIGNMENTS] = assignments
            return await self.async_step_settings()

        options = [
            {"value": phase_id, "label": DEFAULT_PHASE_NAMES[phase_id]}
            for phase_id in PHASE_IDS
        ]

        schema_fields: dict[Any, Any] = {}
        entity_registry = async_get_entity_registry(self.hass)
        description_placeholders: dict[str, str] = {}

        for entity_id in battery_entities:
            key = f"phase_assignment_{entity_id.replace('.', '_')}"
            default_assignment = existing_assignments.get(entity_id, [PHASE_IDS[0]])

            schema_fields[
                vol.Optional(key, default=default_assignment)
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=options,
                    multiple=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )

            entity_entry = entity_registry.async_get(entity_id)
            friendly_name = (
                entity_entry.name
                if entity_entry and entity_entry.name
                else entity_id.split(".")[-1]
            )
            description_placeholders[key] = (
                f"Select the phases that can control {friendly_name}"
            )

        schema = vol.Schema(schema_fields)

        return self.async_show_form(
            step_id="battery_phase_assignment",
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
            vol.Optional(
                CONF_USE_AVERAGE_THRESHOLD,
                default=DEFAULT_USE_AVERAGE_THRESHOLD
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_MIN_CAR_CHARGING_DURATION,
                default=DEFAULT_MIN_CAR_CHARGING_DURATION
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1, max=6, step=1, unit_of_measurement="hours"
                )
            ),
            vol.Optional(
                CONF_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
                default=DEFAULT_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1.1, max=1.5, step=0.1, mode="slider"
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
        """Handle the options step (single-form flow for compatibility)."""
        existing_config = {**self.config_entry.data, **self.config_entry.options}
        working_data: dict[str, Any] = dict(existing_config)

        battery_entities: list[str] = working_data.get(CONF_BATTERY_SOC_ENTITIES, [])
        current_capacities: dict[str, float] = working_data.get(
            CONF_BATTERY_CAPACITIES, {}
        )
        current_phase_mode: str = working_data.get(CONF_PHASE_MODE, PHASE_MODE_SINGLE)
        existing_phases: dict[str, Any] = working_data.get(CONF_PHASES, {})
        existing_assignments: dict[str, list[str]] = working_data.get(
            CONF_BATTERY_PHASE_ASSIGNMENTS, {}
        )

        if user_input is not None:
            updated_options = dict(user_input)
            phase_mode = updated_options.pop(CONF_PHASE_MODE, current_phase_mode)

            submitted_batteries = updated_options.get(
                CONF_BATTERY_SOC_ENTITIES, battery_entities
            )
            battery_entities = list(submitted_batteries) if submitted_batteries else []

            # Extract battery capacities
            battery_capacities: dict[str, float] = {}
            for entity_id in battery_entities:
                capacity_key = f"capacity_{entity_id.replace('.', '_')}"
                if capacity_key in updated_options:
                    battery_capacities[entity_id] = updated_options.pop(capacity_key)

            # Extract battery phase assignments
            battery_phase_assignments: dict[str, list[str]] = {}
            for entity_id in battery_entities:
                assignment_key = f"phase_assignment_{entity_id.replace('.', '_')}"
                value = updated_options.pop(assignment_key, None)
                if value:
                    battery_phase_assignments[entity_id] = list(value)

            # Extract per-phase sensor configuration
            phases_config: dict[str, dict[str, Any]] = {}
            for phase_id in PHASE_IDS:
                solar_key = f"{phase_id}_{CONF_PHASE_SOLAR_ENTITY}"
                consumption_key = f"{phase_id}_{CONF_PHASE_CONSUMPTION_ENTITY}"
                car_key = f"{phase_id}_{CONF_PHASE_CAR_ENTITY}"
                battery_power_key = f"{phase_id}_{CONF_PHASE_BATTERY_POWER_ENTITY}"

                solar_entity = updated_options.pop(solar_key, None)
                consumption_entity = updated_options.pop(consumption_key, None)
                car_entity = updated_options.pop(car_key, None)
                battery_power_entity = updated_options.pop(battery_power_key, None)

                existing_phase = existing_phases.get(phase_id, {})
                if (
                    solar_entity is not None
                    or consumption_entity is not None
                    or car_entity is not None
                    or battery_power_entity is not None
                    or existing_phase
                ):
                    phase_entry = {
                        CONF_PHASE_NAME: existing_phase.get(
                            CONF_PHASE_NAME, DEFAULT_PHASE_NAMES[phase_id]
                        ),
                        CONF_PHASE_SOLAR_ENTITY: solar_entity
                        if solar_entity is not None
                        else existing_phase.get(CONF_PHASE_SOLAR_ENTITY),
                        CONF_PHASE_CONSUMPTION_ENTITY: consumption_entity
                        if consumption_entity is not None
                        else existing_phase.get(CONF_PHASE_CONSUMPTION_ENTITY),
                    }
                    car_value = (
                        car_entity
                        if car_entity is not None
                        else existing_phase.get(CONF_PHASE_CAR_ENTITY)
                    )
                    if car_value:
                        phase_entry[CONF_PHASE_CAR_ENTITY] = car_value

                    battery_power_value = (
                        battery_power_entity
                        if battery_power_entity is not None
                        else existing_phase.get(CONF_PHASE_BATTERY_POWER_ENTITY)
                    )
                    if battery_power_value:
                        phase_entry[CONF_PHASE_BATTERY_POWER_ENTITY] = battery_power_value

                    phases_config[phase_id] = phase_entry

            if phase_mode == PHASE_MODE_THREE:
                # No validation - all per-phase sensors are optional, leave configuration to user
                updated_options[CONF_PHASE_MODE] = PHASE_MODE_THREE
                updated_options[CONF_PHASES] = phases_config
                updated_options[CONF_BATTERY_PHASE_ASSIGNMENTS] = battery_phase_assignments
                # Remove legacy single-phase keys if not provided
                updated_options.pop(CONF_SOLAR_PRODUCTION_ENTITY, None)
                updated_options.pop(CONF_HOUSE_CONSUMPTION_ENTITY, None)
                updated_options.pop(CONF_CAR_CHARGING_POWER_ENTITY, None)
            else:
                updated_options[CONF_PHASE_MODE] = PHASE_MODE_SINGLE
                updated_options.pop(CONF_PHASES, None)
                updated_options.pop(CONF_BATTERY_PHASE_ASSIGNMENTS, None)

            # Attach battery metadata
            updated_options[CONF_BATTERY_SOC_ENTITIES] = battery_entities
            updated_options[CONF_BATTERY_CAPACITIES] = battery_capacities

            return self.async_create_entry(title="", data=updated_options)

        schema_dict: dict[Any, Any] = {
            vol.Required(
                CONF_PHASE_MODE,
                default=current_phase_mode,
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {"label": "Single-phase system", "value": PHASE_MODE_SINGLE},
                        {"label": "Three-phase system (L1/L2/L3)", "value": PHASE_MODE_THREE},
                    ],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            ),
            vol.Optional(
                CONF_NORDPOOL_CONFIG_ENTRY,
                default=working_data.get(CONF_NORDPOOL_CONFIG_ENTRY),
            ): selector.ConfigEntrySelector(
                selector.ConfigEntrySelectorConfig(integration="nordpool")
            ),
            vol.Required(
                CONF_CURRENT_PRICE_ENTITY,
                default=working_data.get(CONF_CURRENT_PRICE_ENTITY),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(
                CONF_HIGHEST_PRICE_ENTITY,
                default=working_data.get(CONF_HIGHEST_PRICE_ENTITY),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(
                CONF_LOWEST_PRICE_ENTITY,
                default=working_data.get(CONF_LOWEST_PRICE_ENTITY),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(
                CONF_NEXT_PRICE_ENTITY,
                default=working_data.get(CONF_NEXT_PRICE_ENTITY),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Required(
                CONF_BATTERY_SOC_ENTITIES,
                default=battery_entities,
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", multiple=True)
            ),
        }

        # Add single-phase or three-phase specific fields
        if current_phase_mode == PHASE_MODE_SINGLE:
            schema_dict.update({
                vol.Optional(
                    CONF_SOLAR_PRODUCTION_ENTITY,
                    default=working_data.get(CONF_SOLAR_PRODUCTION_ENTITY),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(
                    CONF_HOUSE_CONSUMPTION_ENTITY,
                    default=working_data.get(CONF_HOUSE_CONSUMPTION_ENTITY),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(
                    CONF_CAR_CHARGING_POWER_ENTITY,
                    default=working_data.get(CONF_CAR_CHARGING_POWER_ENTITY),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
            })

        # Common optional fields
        schema_dict.update({
            vol.Optional(
                CONF_MONTHLY_GRID_PEAK_ENTITY,
                default=working_data.get(CONF_MONTHLY_GRID_PEAK_ENTITY),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(
                CONF_TRANSPORT_COST_ENTITY,
                default=working_data.get(CONF_TRANSPORT_COST_ENTITY),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(
                CONF_GRID_POWER_ENTITY,
                default=working_data.get(CONF_GRID_POWER_ENTITY),
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor")
            ),
            vol.Optional(
                CONF_MIN_SOC_THRESHOLD,
                default=working_data.get(CONF_MIN_SOC_THRESHOLD, DEFAULT_MIN_SOC),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=100, unit_of_measurement="%")
            ),
            vol.Optional(
                CONF_MAX_SOC_THRESHOLD,
                default=working_data.get(CONF_MAX_SOC_THRESHOLD, DEFAULT_MAX_SOC),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=100, unit_of_measurement="%")
            ),
            vol.Optional(
                CONF_PRICE_THRESHOLD,
                default=working_data.get(CONF_PRICE_THRESHOLD, DEFAULT_PRICE_THRESHOLD),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=1, step=0.01, unit_of_measurement="€/kWh"
                )
            ),
            vol.Optional(
                CONF_PRICE_ADJUSTMENT_MULTIPLIER,
                default=working_data.get(
                    CONF_PRICE_ADJUSTMENT_MULTIPLIER, DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=5, step=0.01)
            ),
            vol.Optional(
                CONF_PRICE_ADJUSTMENT_OFFSET,
                default=working_data.get(
                    CONF_PRICE_ADJUSTMENT_OFFSET, DEFAULT_PRICE_ADJUSTMENT_OFFSET
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=-0.5, max=0.5, step=0.001, unit_of_measurement="€/kWh"
                )
            ),
            vol.Optional(
                CONF_EMERGENCY_SOC_THRESHOLD,
                default=working_data.get(CONF_EMERGENCY_SOC_THRESHOLD, DEFAULT_EMERGENCY_SOC),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=5, max=50, unit_of_measurement="%")
            ),
            vol.Optional(
                CONF_VERY_LOW_PRICE_THRESHOLD,
                default=working_data.get(
                    CONF_VERY_LOW_PRICE_THRESHOLD, DEFAULT_VERY_LOW_PRICE_THRESHOLD
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=10, max=50, unit_of_measurement="%")
            ),
            vol.Optional(
                CONF_SIGNIFICANT_SOLAR_THRESHOLD,
                default=working_data.get(
                    CONF_SIGNIFICANT_SOLAR_THRESHOLD, DEFAULT_SIGNIFICANT_SOLAR_THRESHOLD
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=500, max=5000, step=100, unit_of_measurement="W"
                )
            ),
            vol.Optional(
                CONF_FEEDIN_PRICE_THRESHOLD,
                default=working_data.get(CONF_FEEDIN_PRICE_THRESHOLD, DEFAULT_FEEDIN_PRICE_THRESHOLD),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0.0, max=1.0, step=0.01, unit_of_measurement="€/kWh"
                )
            ),
            vol.Optional(
                CONF_FEEDIN_ADJUSTMENT_MULTIPLIER,
                default=working_data.get(
                    CONF_FEEDIN_ADJUSTMENT_MULTIPLIER, DEFAULT_FEEDIN_ADJUSTMENT_MULTIPLIER
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=-1, max=5, step=0.01)
            ),
            vol.Optional(
                CONF_FEEDIN_ADJUSTMENT_OFFSET,
                default=working_data.get(
                    CONF_FEEDIN_ADJUSTMENT_OFFSET, DEFAULT_FEEDIN_ADJUSTMENT_OFFSET
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=-0.5, max=0.5, step=0.001, unit_of_measurement="€/kWh"
                )
            ),
            vol.Optional(
                CONF_USE_DYNAMIC_THRESHOLD,
                default=working_data.get(CONF_USE_DYNAMIC_THRESHOLD, DEFAULT_USE_DYNAMIC_THRESHOLD),
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_DYNAMIC_THRESHOLD_CONFIDENCE,
                default=working_data.get(
                    CONF_DYNAMIC_THRESHOLD_CONFIDENCE, DEFAULT_DYNAMIC_THRESHOLD_CONFIDENCE
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=30, max=90, step=5, unit_of_measurement="%")
            ),
            vol.Optional(
                CONF_USE_AVERAGE_THRESHOLD,
                default=working_data.get(CONF_USE_AVERAGE_THRESHOLD, DEFAULT_USE_AVERAGE_THRESHOLD),
            ): selector.BooleanSelector(),
            vol.Optional(
                CONF_MIN_CAR_CHARGING_DURATION,
                default=working_data.get(
                    CONF_MIN_CAR_CHARGING_DURATION, DEFAULT_MIN_CAR_CHARGING_DURATION
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=6, step=1, unit_of_measurement="hours")
            ),
            vol.Optional(
                CONF_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
                default=working_data.get(
                    CONF_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER, DEFAULT_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=1.1, max=1.5, step=0.1, mode="slider")
            ),
            vol.Optional(
                CONF_MAX_BATTERY_POWER,
                default=working_data.get(CONF_MAX_BATTERY_POWER, DEFAULT_MAX_BATTERY_POWER),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1000, max=10000, step=500, unit_of_measurement="W"
                )
            ),
            vol.Optional(
                CONF_MAX_CAR_POWER,
                default=working_data.get(CONF_MAX_CAR_POWER, DEFAULT_MAX_CAR_POWER),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1000, max=22000, step=1000, unit_of_measurement="W"
                )
            ),
            vol.Optional(
                CONF_MAX_GRID_POWER,
                default=working_data.get(CONF_MAX_GRID_POWER, DEFAULT_MAX_GRID_POWER),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=3000, max=30000, step=1000, unit_of_measurement="W"
                )
            ),
            vol.Optional(
                CONF_MIN_CAR_CHARGING_THRESHOLD,
                default=working_data.get(
                    CONF_MIN_CAR_CHARGING_THRESHOLD, DEFAULT_MIN_CAR_CHARGING_THRESHOLD
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=50, max=500, step=50, unit_of_measurement="W"
                )
            ),
            vol.Optional(
                CONF_SOLAR_PEAK_EMERGENCY_SOC,
                default=working_data.get(
                    CONF_SOLAR_PEAK_EMERGENCY_SOC, DEFAULT_SOLAR_PEAK_EMERGENCY_SOC
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=15, max=40, unit_of_measurement="%")
            ),
            vol.Optional(
                CONF_PREDICTIVE_CHARGING_MIN_SOC,
                default=working_data.get(
                    CONF_PREDICTIVE_CHARGING_MIN_SOC, DEFAULT_PREDICTIVE_CHARGING_MIN_SOC
                ),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=20, max=60, unit_of_measurement="%")
            ),
            vol.Optional(
                CONF_BASE_GRID_SETPOINT,
                default=working_data.get(CONF_BASE_GRID_SETPOINT, DEFAULT_BASE_GRID_SETPOINT),
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1000, max=10000, step=100, unit_of_measurement="W"
                )
            ),
        })

        # Dynamic per-battery capacity fields
        for entity_id in battery_entities:
            key = f"capacity_{entity_id.replace('.', '_')}"
            default_capacity = current_capacities.get(entity_id, 10.0)
            schema_dict[
                vol.Optional(key, default=default_capacity)
            ] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1.0,
                    max=200.0,
                    step=0.5,
                    unit_of_measurement="kWh",
                    mode=selector.NumberSelectorMode.BOX,
                )
            )

        # Per-battery phase assignments (multi-select) - only relevant in three-phase mode
        if current_phase_mode == PHASE_MODE_THREE:
            phase_options = [
                {"value": phase_id, "label": DEFAULT_PHASE_NAMES[phase_id]} for phase_id in PHASE_IDS
            ]
            for entity_id in battery_entities:
                key = f"phase_assignment_{entity_id.replace('.', '_')}"
                default_assignment = existing_assignments.get(entity_id, [])
                schema_dict[
                    vol.Optional(key, default=default_assignment)
                ] = selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=phase_options,
                        multiple=True,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )

        # Per-phase sensor fields (only in three-phase mode)
        if current_phase_mode == PHASE_MODE_THREE:
            for phase_id in PHASE_IDS:
                phase_config = existing_phases.get(phase_id, {})
                schema_dict[
                    vol.Optional(
                        f"{phase_id}_{CONF_PHASE_SOLAR_ENTITY}",
                        default=phase_config.get(CONF_PHASE_SOLAR_ENTITY),
                    )
                ] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                )
                schema_dict[
                    vol.Optional(
                        f"{phase_id}_{CONF_PHASE_CONSUMPTION_ENTITY}",
                        default=phase_config.get(CONF_PHASE_CONSUMPTION_ENTITY),
                    )
                ] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                )
                schema_dict[
                    vol.Optional(
                        f"{phase_id}_{CONF_PHASE_CAR_ENTITY}",
                        default=phase_config.get(CONF_PHASE_CAR_ENTITY),
                    )
                ] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                )
                schema_dict[
                    vol.Optional(
                        f"{phase_id}_{CONF_PHASE_BATTERY_POWER_ENTITY}",
                        default=phase_config.get(CONF_PHASE_BATTERY_POWER_ENTITY),
                    )
                ] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                )

        schema = vol.Schema(schema_dict)
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            description_placeholders={
                "topology": "Select the grid topology and update entity bindings.",
            },
        )
