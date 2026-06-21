"""Config flow for Electricity Planner integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.entity_registry import async_get as async_get_entity_registry

from .const import (
    CONF_ARBITRAGE_MODE_DEADLINE_HOUR,
    CONF_ARBITRAGE_MODE_MAX_EXPORT_POWER,
    CONF_ARBITRAGE_MODE_RESERVE_SOC,
    CONF_ARBITRAGE_MODE_RESERVE_SOC_SUNNY,
    CONF_BASE_GRID_SETPOINT,
    CONF_BATTERY_CAPACITIES,
    CONF_BATTERY_PHASE_ASSIGNMENTS,
    CONF_BATTERY_SOC_ENTITIES,
    CONF_CAR_CHARGING_POWER_ENTITY,
    CONF_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
    CONF_CAR_USE_BATTERY_ARBITRAGE,
    CONF_CURRENT_PRICE_ENTITY,
    CONF_DYNAMIC_THRESHOLD_CONFIDENCE,
    CONF_EMERGENCY_SOC_THRESHOLD,
    CONF_ENERGY_COST_GSC,
    CONF_ENERGY_COST_WKK,
    CONF_ENERGY_TAX_ACCIJNS,
    CONF_ENERGY_TAX_BIJDRAGE,
    CONF_FEEDIN_ADJUSTMENT_MULTIPLIER,
    CONF_FEEDIN_ADJUSTMENT_OFFSET,
    CONF_BUY_VAT_MULTIPLIER,
    CONF_FEEDIN_PRICE_THRESHOLD,
    CONF_GRID_POWER_ENTITY,
    CONF_HIGHEST_PRICE_ENTITY,
    CONF_HOUSE_CONSUMPTION_ENTITY,
    CONF_INVERTER_DERATING_SOC_BYPASS_THRESHOLD,
    CONF_INVERTER_DERATING_UNUSED_RELEASE_MINUTES,
    CONF_INVERTER_EXPORT_DEADBAND,
    CONF_INVERTER_EXPORT_LIMIT,
    CONF_LOWEST_PRICE_ENTITY,
    CONF_MAX_BATTERY_POWER,
    CONF_MAX_CAR_POWER,
    CONF_MAX_GRID_POWER,
    CONF_MAX_INVERTER_POWER,
    CONF_MAX_SOC_THRESHOLD,
    CONF_MAX_SOC_THRESHOLD_SOLAR,
    CONF_MAX_SOC_THRESHOLD_SUNNY,
    CONF_MIN_CAR_CHARGING_DURATION,
    CONF_MIN_CAR_CHARGING_THRESHOLD,
    CONF_MIN_SOC_THRESHOLD,
    CONF_MONTHLY_GRID_PEAK_ENTITY,
    CONF_NEGATIVE_BUY_THRESHOLD,
    CONF_NEXT_PRICE_ENTITY,
    CONF_NORDPOOL_CONFIG_ENTRY,
    CONF_P1_TARIFF_ENTITY,
    CONF_PHASE_BATTERY_POWER_ENTITY,
    CONF_PHASE_CAR_ENTITY,
    CONF_PHASE_CONSUMPTION_ENTITY,
    CONF_PHASE_GRID_POWER_ENTITY,
    CONF_PHASE_MODE,
    CONF_PHASE_NAME,
    CONF_PHASE_SOLAR_ENTITY,
    CONF_PHASES,
    CONF_PREDICTIVE_CHARGING_MIN_SOC,
    CONF_PRICE_ADJUSTMENT_MULTIPLIER,
    CONF_PRICE_ADJUSTMENT_OFFSET,
    CONF_PRICE_THRESHOLD,
    CONF_SIGNIFICANT_SOLAR_THRESHOLD,
    CONF_SOC_BUFFER_TARGET,
    CONF_SOC_PRICE_MULTIPLIER_MAX,
    CONF_SOLAR_FORECAST_ENTITY_TOMORROW,
    CONF_SOLAR_FORECAST_START_HOUR,
    CONF_SOLAR_FORECAST_TODAY_ENTITY,
    CONF_SOLAR_PRODUCTION_ENTITY,
    CONF_SUNNY_FORECAST_THRESHOLD_KWH,
    CONF_TRANSPORT_COST_DAY,
    CONF_TRANSPORT_COST_ENTITY,
    CONF_TRANSPORT_COST_NIGHT,
    CONF_USE_AVERAGE_THRESHOLD,
    CONF_USE_DYNAMIC_THRESHOLD,
    CONF_VERY_LOW_PRICE_THRESHOLD,
    DEFAULT_ARBITRAGE_MODE_DEADLINE_HOUR,
    DEFAULT_ARBITRAGE_MODE_MAX_EXPORT_POWER,
    DEFAULT_ARBITRAGE_MODE_RESERVE_SOC,
    DEFAULT_ARBITRAGE_MODE_RESERVE_SOC_SUNNY,
    DEFAULT_BASE_GRID_SETPOINT,
    DEFAULT_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
    DEFAULT_CAR_USE_BATTERY_ARBITRAGE,
    DEFAULT_DYNAMIC_THRESHOLD_CONFIDENCE,
    DEFAULT_EMERGENCY_SOC,
    DEFAULT_ENERGY_COST_GSC,
    DEFAULT_ENERGY_COST_WKK,
    DEFAULT_ENERGY_TAX_ACCIJNS,
    DEFAULT_ENERGY_TAX_BIJDRAGE,
    DEFAULT_FEEDIN_ADJUSTMENT_MULTIPLIER,
    DEFAULT_FEEDIN_ADJUSTMENT_OFFSET,
    DEFAULT_BUY_VAT_MULTIPLIER,
    DEFAULT_FEEDIN_PRICE_THRESHOLD,
    DEFAULT_INVERTER_DERATING_SOC_BYPASS_THRESHOLD,
    DEFAULT_INVERTER_DERATING_UNUSED_RELEASE_MINUTES,
    DEFAULT_INVERTER_EXPORT_DEADBAND,
    DEFAULT_INVERTER_EXPORT_LIMIT,
    DEFAULT_MAX_BATTERY_POWER,
    DEFAULT_MAX_CAR_POWER,
    DEFAULT_MAX_GRID_POWER,
    DEFAULT_MAX_INVERTER_POWER,
    DEFAULT_MAX_SOC,
    DEFAULT_MAX_SOC_SOLAR,
    DEFAULT_MAX_SOC_SUNNY,
    DEFAULT_MIN_CAR_CHARGING_DURATION,
    DEFAULT_MIN_CAR_CHARGING_THRESHOLD,
    DEFAULT_MIN_SOC,
    DEFAULT_NEGATIVE_BUY_THRESHOLD,
    DEFAULT_PHASE_NAMES,
    DEFAULT_PREDICTIVE_CHARGING_MIN_SOC,
    DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER,
    DEFAULT_PRICE_ADJUSTMENT_OFFSET,
    DEFAULT_PRICE_THRESHOLD,
    DEFAULT_SIGNIFICANT_SOLAR_THRESHOLD,
    DEFAULT_SOC_BUFFER_TARGET,
    DEFAULT_SOC_PRICE_MULTIPLIER_MAX,
    DEFAULT_SOLAR_FORECAST_START_HOUR,
    DEFAULT_SUNNY_FORECAST_THRESHOLD_KWH,
    DEFAULT_TRANSPORT_COST_DAY,
    DEFAULT_TRANSPORT_COST_NIGHT,
    DEFAULT_USE_AVERAGE_THRESHOLD,
    DEFAULT_USE_DYNAMIC_THRESHOLD,
    DEFAULT_VERY_LOW_PRICE_THRESHOLD,
    DOMAIN,
    PHASE_IDS,
    PHASE_MODE_SINGLE,
    PHASE_MODE_THREE,
)
from .helpers import coerce_integral_range
from .migrations import CURRENT_VERSION

_LOGGER = logging.getLogger(__name__)

# Entity keys that should be normalized (empty string -> None)
OPTIONAL_ENTITY_KEYS = {
    CONF_NORDPOOL_CONFIG_ENTRY,
    CONF_SOLAR_PRODUCTION_ENTITY,
    CONF_HOUSE_CONSUMPTION_ENTITY,
    CONF_CAR_CHARGING_POWER_ENTITY,
    CONF_MONTHLY_GRID_PEAK_ENTITY,
    CONF_TRANSPORT_COST_ENTITY,
    CONF_GRID_POWER_ENTITY,
    CONF_SOLAR_FORECAST_ENTITY_TOMORROW,
    CONF_SOLAR_FORECAST_TODAY_ENTITY,
    CONF_P1_TARIFF_ENTITY,
}


def normalize_entity_values(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize empty strings to None for optional entity fields.

    Home Assistant entity selectors may return empty strings when left blank.
    This ensures we store None instead, which is properly handled downstream.
    """
    result = dict(data)
    for key in OPTIONAL_ENTITY_KEYS:
        if key in result and not result[key]:
            result[key] = None
    return result


def _optional_entity_schema(key: str, default_value: Any) -> vol.Optional:
    """Create vol.Optional schema, avoiding None defaults for entity selectors.

    Entity selectors in Home Assistant don't handle None defaults well,
    so we only set default if there's an actual value.
    """
    if default_value:
        return vol.Optional(key, default=default_value)
    return vol.Optional(key)


def _resolve_optional_entity_value(
    submitted_data: dict[str, Any],
    field_key: str,
    existing_value: Any = None,
    *,
    preserve_when_missing: bool,
) -> Any:
    """Resolve an optional entity selector while allowing explicit clearing."""
    if field_key in submitted_data:
        return submitted_data.get(field_key) or None
    if preserve_when_missing:
        return existing_value
    return None


def _default_sunny_forecast_threshold_kwh(config: dict[str, Any]) -> float:
    """Derive default sunny-day forecast threshold from configured capacities."""
    if CONF_SUNNY_FORECAST_THRESHOLD_KWH in config:
        try:
            return max(0.0, float(config[CONF_SUNNY_FORECAST_THRESHOLD_KWH]))
        except (TypeError, ValueError):
            return DEFAULT_SUNNY_FORECAST_THRESHOLD_KWH

    capacities = config.get(CONF_BATTERY_CAPACITIES, {}) or {}
    total_capacity = 0.0
    for value in capacities.values():
        try:
            capacity = float(value)
        except (TypeError, ValueError):
            continue
        if capacity > 0:
            total_capacity += capacity

    if total_capacity > 0:
        return round(total_capacity / 2.0, 1)
    return DEFAULT_SUNNY_FORECAST_THRESHOLD_KWH


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Electricity Planner."""

    VERSION = CURRENT_VERSION

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
                vol.Required(
                    CONF_PHASE_MODE, default=default_mode
                ): selector.SelectSelector(
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
                    grid_power_key = f"{phase_id}_{CONF_PHASE_GRID_POWER_ENTITY}"
                    battery_power_key = f"{phase_id}_{CONF_PHASE_BATTERY_POWER_ENTITY}"

                    existing_phase = existing_phases.get(phase_id, {})
                    resolved_solar = _resolve_optional_entity_value(
                        processed_input,
                        solar_key,
                        existing_phase.get(CONF_PHASE_SOLAR_ENTITY),
                        preserve_when_missing=True,
                    )
                    processed_input.pop(solar_key, None)
                    resolved_consumption = _resolve_optional_entity_value(
                        processed_input,
                        consumption_key,
                        existing_phase.get(CONF_PHASE_CONSUMPTION_ENTITY),
                        preserve_when_missing=True,
                    )
                    processed_input.pop(consumption_key, None)
                    resolved_car = _resolve_optional_entity_value(
                        processed_input,
                        car_key,
                        existing_phase.get(CONF_PHASE_CAR_ENTITY),
                        preserve_when_missing=True,
                    )
                    processed_input.pop(car_key, None)
                    resolved_grid_power = _resolve_optional_entity_value(
                        processed_input,
                        grid_power_key,
                        existing_phase.get(CONF_PHASE_GRID_POWER_ENTITY),
                        preserve_when_missing=True,
                    )
                    processed_input.pop(grid_power_key, None)
                    resolved_battery_power = _resolve_optional_entity_value(
                        processed_input,
                        battery_power_key,
                        existing_phase.get(CONF_PHASE_BATTERY_POWER_ENTITY),
                        preserve_when_missing=True,
                    )
                    processed_input.pop(battery_power_key, None)

                    if any(
                        value is not None
                        for value in (
                            resolved_solar,
                            resolved_consumption,
                            resolved_car,
                            resolved_grid_power,
                            resolved_battery_power,
                        )
                    ):
                        phase_entry = {
                            CONF_PHASE_NAME: existing_phase.get(
                                CONF_PHASE_NAME, DEFAULT_PHASE_NAMES[phase_id]
                            ),
                        }
                        if resolved_solar is not None:
                            phase_entry[CONF_PHASE_SOLAR_ENTITY] = resolved_solar
                        if resolved_consumption is not None:
                            phase_entry[CONF_PHASE_CONSUMPTION_ENTITY] = (
                                resolved_consumption
                            )
                        if resolved_car is not None:
                            phase_entry[CONF_PHASE_CAR_ENTITY] = resolved_car
                        if resolved_grid_power is not None:
                            phase_entry[CONF_PHASE_GRID_POWER_ENTITY] = (
                                resolved_grid_power
                            )
                        if resolved_battery_power is not None:
                            phase_entry[CONF_PHASE_BATTERY_POWER_ENTITY] = (
                                resolved_battery_power
                            )

                        phases_config[phase_id] = phase_entry

                self.data[CONF_PHASES] = phases_config
            else:
                # Ensure phase-specific configuration is cleared when returning to single-phase mode
                self.data.pop(CONF_PHASES, None)
                self.data.pop(CONF_BATTERY_PHASE_ASSIGNMENTS, None)

            self.data.update(normalize_entity_values(processed_input))
            return await self.async_step_battery_capacities()

        schema_dict: dict[Any, Any] = {
            _optional_entity_schema(
                CONF_NORDPOOL_CONFIG_ENTRY,
                self.data.get(CONF_NORDPOOL_CONFIG_ENTRY),
            ): selector.ConfigEntrySelector(
                selector.ConfigEntrySelectorConfig(integration="nordpool")
            ),
            vol.Required(
                CONF_CURRENT_PRICE_ENTITY,
                default=self.data.get(CONF_CURRENT_PRICE_ENTITY),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(
                CONF_HIGHEST_PRICE_ENTITY,
                default=self.data.get(CONF_HIGHEST_PRICE_ENTITY),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(
                CONF_LOWEST_PRICE_ENTITY,
                default=self.data.get(CONF_LOWEST_PRICE_ENTITY),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(
                CONF_NEXT_PRICE_ENTITY,
                default=self.data.get(CONF_NEXT_PRICE_ENTITY),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
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
                    _optional_entity_schema(
                        CONF_SOLAR_PRODUCTION_ENTITY,
                        self.data.get(CONF_SOLAR_PRODUCTION_ENTITY),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    _optional_entity_schema(
                        CONF_HOUSE_CONSUMPTION_ENTITY,
                        self.data.get(CONF_HOUSE_CONSUMPTION_ENTITY),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    _optional_entity_schema(
                        CONF_CAR_CHARGING_POWER_ENTITY,
                        self.data.get(CONF_CAR_CHARGING_POWER_ENTITY),
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
                grid_power_key = f"{phase_id}_{CONF_PHASE_GRID_POWER_ENTITY}"
                battery_power_key = f"{phase_id}_{CONF_PHASE_BATTERY_POWER_ENTITY}"

                schema_dict[
                    _optional_entity_schema(
                        solar_key, existing.get(CONF_PHASE_SOLAR_ENTITY)
                    )
                ] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                )
                schema_dict[
                    _optional_entity_schema(
                        consumption_key, existing.get(CONF_PHASE_CONSUMPTION_ENTITY)
                    )
                ] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                )
                schema_dict[
                    _optional_entity_schema(
                        car_key, existing.get(CONF_PHASE_CAR_ENTITY)
                    )
                ] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                )
                schema_dict[
                    _optional_entity_schema(
                        grid_power_key, existing.get(CONF_PHASE_GRID_POWER_ENTITY)
                    )
                ] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                )
                schema_dict[
                    _optional_entity_schema(
                        battery_power_key, existing.get(CONF_PHASE_BATTERY_POWER_ENTITY)
                    )
                ] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                )

        schema_dict.update(
            {
                _optional_entity_schema(
                    CONF_MONTHLY_GRID_PEAK_ENTITY,
                    self.data.get(CONF_MONTHLY_GRID_PEAK_ENTITY),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                _optional_entity_schema(
                    CONF_P1_TARIFF_ENTITY,
                    self.data.get(CONF_P1_TARIFF_ENTITY),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(
                    CONF_TRANSPORT_COST_DAY,
                    default=self.data.get(
                        CONF_TRANSPORT_COST_DAY, DEFAULT_TRANSPORT_COST_DAY
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=1,
                        step="any",
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="€/kWh",
                    )
                ),
                vol.Optional(
                    CONF_TRANSPORT_COST_NIGHT,
                    default=self.data.get(
                        CONF_TRANSPORT_COST_NIGHT, DEFAULT_TRANSPORT_COST_NIGHT
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=1,
                        step="any",
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="€/kWh",
                    )
                ),
                vol.Optional(
                    CONF_ENERGY_TAX_ACCIJNS,
                    default=self.data.get(
                        CONF_ENERGY_TAX_ACCIJNS, DEFAULT_ENERGY_TAX_ACCIJNS
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=1,
                        step="any",
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="€/kWh",
                    )
                ),
                vol.Optional(
                    CONF_ENERGY_TAX_BIJDRAGE,
                    default=self.data.get(
                        CONF_ENERGY_TAX_BIJDRAGE, DEFAULT_ENERGY_TAX_BIJDRAGE
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=1,
                        step="any",
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="€/kWh",
                    )
                ),
                vol.Optional(
                    CONF_ENERGY_COST_GSC,
                    default=self.data.get(
                        CONF_ENERGY_COST_GSC, DEFAULT_ENERGY_COST_GSC
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=1,
                        step="any",
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="€/kWh",
                    )
                ),
                vol.Optional(
                    CONF_ENERGY_COST_WKK,
                    default=self.data.get(
                        CONF_ENERGY_COST_WKK, DEFAULT_ENERGY_COST_WKK
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=1,
                        step="any",
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="€/kWh",
                    )
                ),
                _optional_entity_schema(
                    CONF_GRID_POWER_ENTITY,
                    self.data.get(CONF_GRID_POWER_ENTITY),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                _optional_entity_schema(
                    CONF_SOLAR_FORECAST_ENTITY_TOMORROW,
                    self.data.get(CONF_SOLAR_FORECAST_ENTITY_TOMORROW),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                _optional_entity_schema(
                    CONF_SOLAR_FORECAST_TODAY_ENTITY,
                    self.data.get(CONF_SOLAR_FORECAST_TODAY_ENTITY),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
            }
        )

        schema = vol.Schema(schema_dict)

        return self.async_show_form(
            step_id="entities",
            data_schema=schema,
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
            if self.data.get(CONF_PHASE_MODE) == PHASE_MODE_THREE and battery_entities:
                return await self.async_step_battery_phase_assignment()
            return await self.async_step_settings()

        battery_entities = self.data.get(CONF_BATTERY_SOC_ENTITIES, [])

        if not battery_entities:
            self.data[CONF_BATTERY_CAPACITIES] = {}
            return await self.async_step_settings()

        current_capacities = self.data.get(CONF_BATTERY_CAPACITIES, {}) or {}
        battery_capacities = {}
        for entity_id in battery_entities:
            entity_key = f"capacity_{entity_id.replace('.', '_')}"
            default_capacity = current_capacities.get(entity_id, 10.0)
            battery_capacities[vol.Optional(entity_key, default=default_capacity)] = (
                selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1.0,
                        max=200.0,
                        step=0.5,
                        unit_of_measurement="kWh",
                        mode=selector.NumberSelectorMode.BOX,
                    )
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
            friendly_name = (
                entity_entry.name
                if entity_entry and entity_entry.name
                else entity_id.split(".")[-1]
            )
            entity_key = f"capacity_{entity_id.replace('.', '_')}"
            description_placeholders[entity_key] = (
                f"Nominal capacity for {friendly_name}"
            )

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
                selected = user_input.get(key) or existing_assignments.get(entity_id)
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

            schema_fields[vol.Optional(key, default=default_assignment)] = (
                selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options,
                        multiple=True,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
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

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_MIN_SOC_THRESHOLD,
                    default=self.data.get(CONF_MIN_SOC_THRESHOLD, DEFAULT_MIN_SOC),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=100, unit_of_measurement="%"
                    )
                ),
                vol.Optional(
                    CONF_MAX_SOC_THRESHOLD,
                    default=self.data.get(CONF_MAX_SOC_THRESHOLD, DEFAULT_MAX_SOC),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=100, unit_of_measurement="%"
                    )
                ),
                vol.Optional(
                    CONF_ARBITRAGE_MODE_RESERVE_SOC,
                    default=self.data.get(
                        CONF_ARBITRAGE_MODE_RESERVE_SOC,
                        DEFAULT_ARBITRAGE_MODE_RESERVE_SOC,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=100, unit_of_measurement="%"
                    )
                ),
                vol.Optional(
                    CONF_ARBITRAGE_MODE_RESERVE_SOC_SUNNY,
                    default=self.data.get(
                        CONF_ARBITRAGE_MODE_RESERVE_SOC_SUNNY,
                        DEFAULT_ARBITRAGE_MODE_RESERVE_SOC_SUNNY,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=100, unit_of_measurement="%"
                    )
                ),
                vol.Optional(
                    CONF_ARBITRAGE_MODE_DEADLINE_HOUR,
                    default=self.data.get(
                        CONF_ARBITRAGE_MODE_DEADLINE_HOUR,
                        DEFAULT_ARBITRAGE_MODE_DEADLINE_HOUR,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=23, step=1, unit_of_measurement="hour", mode="slider"
                    )
                ),
                vol.Optional(
                    CONF_NEGATIVE_BUY_THRESHOLD,
                    default=self.data.get(
                        CONF_NEGATIVE_BUY_THRESHOLD,
                        DEFAULT_NEGATIVE_BUY_THRESHOLD,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=-1, max=1, step=0.01, unit_of_measurement="€/kWh"
                    )
                ),
                vol.Optional(
                    CONF_SOLAR_FORECAST_START_HOUR,
                    default=self.data.get(
                        CONF_SOLAR_FORECAST_START_HOUR,
                        DEFAULT_SOLAR_FORECAST_START_HOUR,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=12, max=23, step=1, mode="slider")
                ),
                vol.Optional(
                    CONF_MAX_SOC_THRESHOLD_SUNNY,
                    default=self.data.get(
                        CONF_MAX_SOC_THRESHOLD_SUNNY, DEFAULT_MAX_SOC_SUNNY
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=100, unit_of_measurement="%"
                    )
                ),
                vol.Optional(
                    CONF_MAX_SOC_THRESHOLD_SOLAR,
                    default=self.data.get(
                        CONF_MAX_SOC_THRESHOLD_SOLAR, DEFAULT_MAX_SOC_SOLAR
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=100, unit_of_measurement="%"
                    )
                ),
                vol.Optional(
                    CONF_SUNNY_FORECAST_THRESHOLD_KWH,
                    default=_default_sunny_forecast_threshold_kwh(self.data),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=100,
                        step=0.5,
                        unit_of_measurement="kWh",
                        mode="slider",
                    )
                ),
                vol.Optional(
                    CONF_PRICE_THRESHOLD,
                    default=self.data.get(
                        CONF_PRICE_THRESHOLD, DEFAULT_PRICE_THRESHOLD
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=1, step=0.01, unit_of_measurement="€/kWh"
                    )
                ),
                vol.Optional(
                    CONF_PRICE_ADJUSTMENT_MULTIPLIER,
                    default=self.data.get(
                        CONF_PRICE_ADJUSTMENT_MULTIPLIER,
                        DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0, max=5, step=0.01)
                ),
                vol.Optional(
                    CONF_PRICE_ADJUSTMENT_OFFSET,
                    default=self.data.get(
                        CONF_PRICE_ADJUSTMENT_OFFSET, DEFAULT_PRICE_ADJUSTMENT_OFFSET
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=-0.5, max=0.5, step=0.001, unit_of_measurement="€/kWh"
                    )
                ),
                vol.Optional(
                    CONF_EMERGENCY_SOC_THRESHOLD,
                    default=self.data.get(
                        CONF_EMERGENCY_SOC_THRESHOLD, DEFAULT_EMERGENCY_SOC
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=5, max=50, unit_of_measurement="%"
                    )
                ),
                vol.Optional(
                    CONF_SOC_BUFFER_TARGET,
                    default=self.data.get(
                        CONF_SOC_BUFFER_TARGET, DEFAULT_SOC_BUFFER_TARGET
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=30, max=80, unit_of_measurement="%"
                    )
                ),
                vol.Optional(
                    CONF_SOC_PRICE_MULTIPLIER_MAX,
                    default=self.data.get(
                        CONF_SOC_PRICE_MULTIPLIER_MAX, DEFAULT_SOC_PRICE_MULTIPLIER_MAX
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1.0, max=2.0, step=0.05)
                ),
                vol.Optional(
                    CONF_VERY_LOW_PRICE_THRESHOLD,
                    default=self.data.get(
                        CONF_VERY_LOW_PRICE_THRESHOLD, DEFAULT_VERY_LOW_PRICE_THRESHOLD
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=10, max=50, unit_of_measurement="%"
                    )
                ),
                vol.Optional(
                    CONF_SIGNIFICANT_SOLAR_THRESHOLD,
                    default=self.data.get(
                        CONF_SIGNIFICANT_SOLAR_THRESHOLD,
                        DEFAULT_SIGNIFICANT_SOLAR_THRESHOLD,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=500, max=5000, step=100, unit_of_measurement="W"
                    )
                ),
                vol.Optional(
                    CONF_FEEDIN_PRICE_THRESHOLD,
                    default=self.data.get(
                        CONF_FEEDIN_PRICE_THRESHOLD, DEFAULT_FEEDIN_PRICE_THRESHOLD
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0.0, max=1.0, step=0.01, unit_of_measurement="€/kWh"
                    )
                ),
                vol.Optional(
                    CONF_FEEDIN_ADJUSTMENT_MULTIPLIER,
                    default=self.data.get(
                        CONF_FEEDIN_ADJUSTMENT_MULTIPLIER,
                        DEFAULT_FEEDIN_ADJUSTMENT_MULTIPLIER,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=-1, max=5, step=0.01)
                ),
                vol.Optional(
                    CONF_FEEDIN_ADJUSTMENT_OFFSET,
                    default=self.data.get(
                        CONF_FEEDIN_ADJUSTMENT_OFFSET, DEFAULT_FEEDIN_ADJUSTMENT_OFFSET
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=-0.5, max=0.5, step=0.001, unit_of_measurement="€/kWh"
                    )
                ),
                vol.Optional(
                    CONF_BUY_VAT_MULTIPLIER,
                    default=self.data.get(
                        CONF_BUY_VAT_MULTIPLIER, DEFAULT_BUY_VAT_MULTIPLIER
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1.0, max=2.0, step=0.01)
                ),

                vol.Optional(
                    CONF_USE_DYNAMIC_THRESHOLD,
                    default=self.data.get(
                        CONF_USE_DYNAMIC_THRESHOLD, DEFAULT_USE_DYNAMIC_THRESHOLD
                    ),
                ): selector.BooleanSelector(),
                vol.Optional(
                    CONF_DYNAMIC_THRESHOLD_CONFIDENCE,
                    default=self.data.get(
                        CONF_DYNAMIC_THRESHOLD_CONFIDENCE,
                        DEFAULT_DYNAMIC_THRESHOLD_CONFIDENCE,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=30, max=90, step=5, unit_of_measurement="%"
                    )
                ),
                vol.Optional(
                    CONF_USE_AVERAGE_THRESHOLD,
                    default=self.data.get(
                        CONF_USE_AVERAGE_THRESHOLD, DEFAULT_USE_AVERAGE_THRESHOLD
                    ),
                ): selector.BooleanSelector(),
                vol.Optional(
                    CONF_MIN_CAR_CHARGING_DURATION,
                    default=self.data.get(
                        CONF_MIN_CAR_CHARGING_DURATION,
                        DEFAULT_MIN_CAR_CHARGING_DURATION,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1, max=6, step=1, unit_of_measurement="hours"
                    )
                ),
                vol.Optional(
                    CONF_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
                    default=self.data.get(
                        CONF_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
                        DEFAULT_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1.1, max=1.5, step=0.1, mode="slider"
                    )
                ),
                vol.Optional(
                    CONF_CAR_USE_BATTERY_ARBITRAGE,
                    default=self.data.get(
                        CONF_CAR_USE_BATTERY_ARBITRAGE,
                        DEFAULT_CAR_USE_BATTERY_ARBITRAGE,
                    ),
                ): selector.BooleanSelector(),
                vol.Optional(
                    CONF_MAX_INVERTER_POWER,
                    default=self.data.get(
                        CONF_MAX_INVERTER_POWER, DEFAULT_MAX_INVERTER_POWER
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=500, max=50000, step=100, unit_of_measurement="W"
                    )
                ),
                vol.Optional(
                    CONF_INVERTER_EXPORT_LIMIT,
                    default=self.data.get(
                        CONF_INVERTER_EXPORT_LIMIT, DEFAULT_INVERTER_EXPORT_LIMIT
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=5000, step=10, unit_of_measurement="W"
                    )
                ),
                vol.Optional(
                    CONF_INVERTER_EXPORT_DEADBAND,
                    default=self.data.get(
                        CONF_INVERTER_EXPORT_DEADBAND, DEFAULT_INVERTER_EXPORT_DEADBAND
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=500, step=5, unit_of_measurement="W"
                    )
                ),
                vol.Optional(
                    CONF_INVERTER_DERATING_UNUSED_RELEASE_MINUTES,
                    default=self.data.get(
                        CONF_INVERTER_DERATING_UNUSED_RELEASE_MINUTES,
                        DEFAULT_INVERTER_DERATING_UNUSED_RELEASE_MINUTES,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=120, step=1, unit_of_measurement="minutes"
                    )
                ),
                vol.Optional(
                    CONF_INVERTER_DERATING_SOC_BYPASS_THRESHOLD,
                    default=self.data.get(
                        CONF_INVERTER_DERATING_SOC_BYPASS_THRESHOLD,
                        DEFAULT_INVERTER_DERATING_SOC_BYPASS_THRESHOLD,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=100, step=1, unit_of_measurement="%"
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="settings",
            data_schema=schema,
        )

    async def async_step_safety_limits(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the safety limits step - Power and SOC Safety Limits."""
        errors = {}
        validation_error_text = ""
        if user_input is not None:
            self.data.update(user_input)

            # Normalize empty strings to None for optional entity fields
            self.data = normalize_entity_values(self.data)

            # Validate configuration consistency
            validation_errors = validate_config_consistency(self.data)
            if validation_errors:
                errors["base"] = "invalid_config"
                validation_error_text = "; ".join(validation_errors)
                _LOGGER.warning(
                    "Configuration validation errors: %s", validation_errors
                )
            else:
                return self.async_create_entry(
                    title="Electricity Planner", data=self.data
                )

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_MAX_BATTERY_POWER,
                    default=self.data.get(
                        CONF_MAX_BATTERY_POWER, DEFAULT_MAX_BATTERY_POWER
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1000, max=10000, step=500, unit_of_measurement="W"
                    )
                ),
                vol.Optional(
                    CONF_MAX_CAR_POWER,
                    default=self.data.get(CONF_MAX_CAR_POWER, DEFAULT_MAX_CAR_POWER),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1000, max=22000, step=1000, unit_of_measurement="W"
                    )
                ),
                vol.Optional(
                    CONF_MAX_GRID_POWER,
                    default=self.data.get(CONF_MAX_GRID_POWER, DEFAULT_MAX_GRID_POWER),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=3000, max=30000, step=1000, unit_of_measurement="W"
                    )
                ),
                vol.Optional(
                    CONF_MIN_CAR_CHARGING_THRESHOLD,
                    default=self.data.get(
                        CONF_MIN_CAR_CHARGING_THRESHOLD,
                        DEFAULT_MIN_CAR_CHARGING_THRESHOLD,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=50, max=500, step=50, unit_of_measurement="W"
                    )
                ),
                vol.Optional(
                    CONF_PREDICTIVE_CHARGING_MIN_SOC,
                    default=self.data.get(
                        CONF_PREDICTIVE_CHARGING_MIN_SOC,
                        DEFAULT_PREDICTIVE_CHARGING_MIN_SOC,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=20, max=60, unit_of_measurement="%"
                    )
                ),
                vol.Optional(
                    CONF_BASE_GRID_SETPOINT,
                    default=self.data.get(
                        CONF_BASE_GRID_SETPOINT, DEFAULT_BASE_GRID_SETPOINT
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1000, max=10000, step=100, unit_of_measurement="W"
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="safety_limits",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "validation_errors": (
                    f"Validation error: {validation_error_text}"
                    if validation_error_text
                    else ""
                ),
            },
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        """Return the options flow."""
        return OptionsFlowHandler()


def validate_config_consistency(config: dict[str, Any]) -> list[str]:
    """Validate configuration for logical consistency.

    Args:
        config: Configuration dictionary to validate

    Returns:
        List of error messages (empty if valid)
    """
    errors = []

    # SOC threshold validation
    min_soc = config.get(CONF_MIN_SOC_THRESHOLD, DEFAULT_MIN_SOC)
    max_soc = config.get(CONF_MAX_SOC_THRESHOLD, DEFAULT_MAX_SOC)
    max_soc_sunny = config.get(CONF_MAX_SOC_THRESHOLD_SUNNY, DEFAULT_MAX_SOC_SUNNY)
    emergency_soc = config.get(CONF_EMERGENCY_SOC_THRESHOLD, DEFAULT_EMERGENCY_SOC)

    if min_soc >= max_soc:
        errors.append(f"min_soc ({min_soc}%) must be less than max_soc ({max_soc}%)")

    if max_soc_sunny > max_soc:
        errors.append(
            "max_soc_threshold_sunny "
            f"({max_soc_sunny}%) must be less than or equal to max_soc ({max_soc}%)"
        )

    max_soc_solar = config.get(CONF_MAX_SOC_THRESHOLD_SOLAR, DEFAULT_MAX_SOC_SOLAR)
    if not 0 <= max_soc_solar <= 100:
        errors.append(
            "max_soc_threshold_solar " f"({max_soc_solar}%) must be between 0 and 100"
        )
    if max_soc_solar < min_soc:
        errors.append(
            "max_soc_threshold_solar "
            f"({max_soc_solar}%) must be greater than or equal to min_soc ({min_soc}%)"
        )

    if emergency_soc > min_soc:
        errors.append(
            f"emergency_soc ({emergency_soc}%) should be below min_soc ({min_soc}%)"
        )

    # Power limit validation
    max_battery_power = config.get(CONF_MAX_BATTERY_POWER, DEFAULT_MAX_BATTERY_POWER)
    max_grid_power = config.get(CONF_MAX_GRID_POWER, DEFAULT_MAX_GRID_POWER)

    if max_battery_power > max_grid_power:
        errors.append(
            f"max_battery_power ({max_battery_power}W) cannot exceed max_grid_power ({max_grid_power}W)"
        )

    # Price threshold validation
    price_threshold = config.get(CONF_PRICE_THRESHOLD, DEFAULT_PRICE_THRESHOLD)
    if price_threshold < 0:
        errors.append(f"price_threshold ({price_threshold}€/kWh) cannot be negative")

    # Very low price threshold validation
    very_low_price = config.get(
        CONF_VERY_LOW_PRICE_THRESHOLD, DEFAULT_VERY_LOW_PRICE_THRESHOLD
    )
    if not 0 <= very_low_price <= 100:
        errors.append(
            f"very_low_price_threshold ({very_low_price}%) must be between 0 and 100"
        )

    sunny_forecast_threshold_kwh = config.get(
        CONF_SUNNY_FORECAST_THRESHOLD_KWH,
        DEFAULT_SUNNY_FORECAST_THRESHOLD_KWH,
    )
    if sunny_forecast_threshold_kwh < 0:
        errors.append(
            "sunny_forecast_threshold_kwh "
            f"({sunny_forecast_threshold_kwh}kWh) cannot be negative"
        )

    arbitrage_mode_deadline_hour = config.get(
        CONF_ARBITRAGE_MODE_DEADLINE_HOUR,
        DEFAULT_ARBITRAGE_MODE_DEADLINE_HOUR,
    )
    normalized_deadline_hour = coerce_integral_range(
        arbitrage_mode_deadline_hour,
        min_value=0,
        max_value=23,
    )
    if normalized_deadline_hour is None:
        errors.append(
            "arbitrage_mode_deadline_hour "
            f"({arbitrage_mode_deadline_hour}) must be an integer between 0 and 23"
        )

    return errors


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Electricity Planner."""

    def __init__(self) -> None:
        """Initialize the options flow."""
        self.data: dict[str, Any] = {}

    def _ensure_loaded(self) -> None:
        """Load the effective config once for this flow."""
        if not self.data:
            self.data = {
                **self.config_entry.data,
                **self.config_entry.options,
            }

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle topology selection for options flow."""
        self._ensure_loaded()
        if user_input is not None:
            self.data[CONF_PHASE_MODE] = user_input.get(
                CONF_PHASE_MODE, PHASE_MODE_SINGLE
            )
            return await self.async_step_entities()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_PHASE_MODE,
                    default=self.data.get(CONF_PHASE_MODE, PHASE_MODE_SINGLE),
                ): selector.SelectSelector(
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
        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_entities(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Collect entity configuration during options flow."""
        self._ensure_loaded()
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
                    grid_power_key = f"{phase_id}_{CONF_PHASE_GRID_POWER_ENTITY}"
                    battery_power_key = f"{phase_id}_{CONF_PHASE_BATTERY_POWER_ENTITY}"

                    existing_phase = existing_phases.get(phase_id, {})
                    resolved_solar = _resolve_optional_entity_value(
                        processed_input,
                        solar_key,
                        existing_phase.get(CONF_PHASE_SOLAR_ENTITY),
                        preserve_when_missing=True,
                    )
                    processed_input.pop(solar_key, None)
                    resolved_consumption = _resolve_optional_entity_value(
                        processed_input,
                        consumption_key,
                        existing_phase.get(CONF_PHASE_CONSUMPTION_ENTITY),
                        preserve_when_missing=True,
                    )
                    processed_input.pop(consumption_key, None)
                    resolved_car = _resolve_optional_entity_value(
                        processed_input,
                        car_key,
                        existing_phase.get(CONF_PHASE_CAR_ENTITY),
                        preserve_when_missing=True,
                    )
                    processed_input.pop(car_key, None)
                    resolved_grid_power = _resolve_optional_entity_value(
                        processed_input,
                        grid_power_key,
                        existing_phase.get(CONF_PHASE_GRID_POWER_ENTITY),
                        preserve_when_missing=True,
                    )
                    processed_input.pop(grid_power_key, None)
                    resolved_battery_power = _resolve_optional_entity_value(
                        processed_input,
                        battery_power_key,
                        existing_phase.get(CONF_PHASE_BATTERY_POWER_ENTITY),
                        preserve_when_missing=True,
                    )
                    processed_input.pop(battery_power_key, None)

                    if any(
                        value is not None
                        for value in (
                            resolved_solar,
                            resolved_consumption,
                            resolved_car,
                            resolved_grid_power,
                            resolved_battery_power,
                        )
                    ):
                        phase_entry = {
                            CONF_PHASE_NAME: existing_phase.get(
                                CONF_PHASE_NAME, DEFAULT_PHASE_NAMES[phase_id]
                            ),
                        }
                        if resolved_solar is not None:
                            phase_entry[CONF_PHASE_SOLAR_ENTITY] = resolved_solar
                        if resolved_consumption is not None:
                            phase_entry[CONF_PHASE_CONSUMPTION_ENTITY] = (
                                resolved_consumption
                            )
                        if resolved_car is not None:
                            phase_entry[CONF_PHASE_CAR_ENTITY] = resolved_car
                        if resolved_grid_power is not None:
                            phase_entry[CONF_PHASE_GRID_POWER_ENTITY] = (
                                resolved_grid_power
                            )
                        if resolved_battery_power is not None:
                            phase_entry[CONF_PHASE_BATTERY_POWER_ENTITY] = (
                                resolved_battery_power
                            )
                        phases_config[phase_id] = phase_entry

                self.data[CONF_PHASES] = phases_config
            else:
                self.data.pop(CONF_PHASES, None)
                self.data.pop(CONF_BATTERY_PHASE_ASSIGNMENTS, None)

            self.data.update(normalize_entity_values(processed_input))
            return await self.async_step_battery_capacities()

        schema_dict: dict[Any, Any] = {
            _optional_entity_schema(
                CONF_NORDPOOL_CONFIG_ENTRY,
                self.data.get(CONF_NORDPOOL_CONFIG_ENTRY),
            ): selector.ConfigEntrySelector(
                selector.ConfigEntrySelectorConfig(integration="nordpool")
            ),
            vol.Required(
                CONF_CURRENT_PRICE_ENTITY,
                default=self.data.get(CONF_CURRENT_PRICE_ENTITY),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(
                CONF_HIGHEST_PRICE_ENTITY,
                default=self.data.get(CONF_HIGHEST_PRICE_ENTITY),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(
                CONF_LOWEST_PRICE_ENTITY,
                default=self.data.get(CONF_LOWEST_PRICE_ENTITY),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
            vol.Required(
                CONF_NEXT_PRICE_ENTITY,
                default=self.data.get(CONF_NEXT_PRICE_ENTITY),
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
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
                    _optional_entity_schema(
                        CONF_SOLAR_PRODUCTION_ENTITY,
                        self.data.get(CONF_SOLAR_PRODUCTION_ENTITY),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    _optional_entity_schema(
                        CONF_HOUSE_CONSUMPTION_ENTITY,
                        self.data.get(CONF_HOUSE_CONSUMPTION_ENTITY),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    _optional_entity_schema(
                        CONF_CAR_CHARGING_POWER_ENTITY,
                        self.data.get(CONF_CAR_CHARGING_POWER_ENTITY),
                    ): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                }
            )
        else:
            phases_config = self.data.get(CONF_PHASES, {})
            for phase_id in PHASE_IDS:
                existing = phases_config.get(phase_id, {})
                schema_dict[
                    _optional_entity_schema(
                        f"{phase_id}_{CONF_PHASE_SOLAR_ENTITY}",
                        existing.get(CONF_PHASE_SOLAR_ENTITY),
                    )
                ] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                )
                schema_dict[
                    _optional_entity_schema(
                        f"{phase_id}_{CONF_PHASE_CONSUMPTION_ENTITY}",
                        existing.get(CONF_PHASE_CONSUMPTION_ENTITY),
                    )
                ] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                )
                schema_dict[
                    _optional_entity_schema(
                        f"{phase_id}_{CONF_PHASE_CAR_ENTITY}",
                        existing.get(CONF_PHASE_CAR_ENTITY),
                    )
                ] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                )
                schema_dict[
                    _optional_entity_schema(
                        f"{phase_id}_{CONF_PHASE_GRID_POWER_ENTITY}",
                        existing.get(CONF_PHASE_GRID_POWER_ENTITY),
                    )
                ] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                )
                schema_dict[
                    _optional_entity_schema(
                        f"{phase_id}_{CONF_PHASE_BATTERY_POWER_ENTITY}",
                        existing.get(CONF_PHASE_BATTERY_POWER_ENTITY),
                    )
                ] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                )

        schema_dict.update(
            {
                _optional_entity_schema(
                    CONF_MONTHLY_GRID_PEAK_ENTITY,
                    self.data.get(CONF_MONTHLY_GRID_PEAK_ENTITY),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                _optional_entity_schema(
                    CONF_P1_TARIFF_ENTITY,
                    self.data.get(CONF_P1_TARIFF_ENTITY),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(
                    CONF_TRANSPORT_COST_DAY,
                    default=self.data.get(
                        CONF_TRANSPORT_COST_DAY, DEFAULT_TRANSPORT_COST_DAY
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=1,
                        step="any",
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="€/kWh",
                    )
                ),
                vol.Optional(
                    CONF_TRANSPORT_COST_NIGHT,
                    default=self.data.get(
                        CONF_TRANSPORT_COST_NIGHT, DEFAULT_TRANSPORT_COST_NIGHT
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=1,
                        step="any",
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="€/kWh",
                    )
                ),
                vol.Optional(
                    CONF_ENERGY_TAX_ACCIJNS,
                    default=self.data.get(
                        CONF_ENERGY_TAX_ACCIJNS, DEFAULT_ENERGY_TAX_ACCIJNS
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=1,
                        step="any",
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="€/kWh",
                    )
                ),
                vol.Optional(
                    CONF_ENERGY_TAX_BIJDRAGE,
                    default=self.data.get(
                        CONF_ENERGY_TAX_BIJDRAGE, DEFAULT_ENERGY_TAX_BIJDRAGE
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=1,
                        step="any",
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="€/kWh",
                    )
                ),
                vol.Optional(
                    CONF_ENERGY_COST_GSC,
                    default=self.data.get(
                        CONF_ENERGY_COST_GSC, DEFAULT_ENERGY_COST_GSC
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=1,
                        step="any",
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="€/kWh",
                    )
                ),
                vol.Optional(
                    CONF_ENERGY_COST_WKK,
                    default=self.data.get(
                        CONF_ENERGY_COST_WKK, DEFAULT_ENERGY_COST_WKK
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=1,
                        step="any",
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="€/kWh",
                    )
                ),
                _optional_entity_schema(
                    CONF_GRID_POWER_ENTITY,
                    self.data.get(CONF_GRID_POWER_ENTITY),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                _optional_entity_schema(
                    CONF_SOLAR_FORECAST_ENTITY_TOMORROW,
                    self.data.get(CONF_SOLAR_FORECAST_ENTITY_TOMORROW),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                _optional_entity_schema(
                    CONF_SOLAR_FORECAST_TODAY_ENTITY,
                    self.data.get(CONF_SOLAR_FORECAST_TODAY_ENTITY),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
            }
        )
        return self.async_show_form(
            step_id="entities", data_schema=vol.Schema(schema_dict)
        )

    async def async_step_battery_capacities(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure battery capacities in options flow."""
        self._ensure_loaded()
        if user_input is not None:
            battery_entities = self.data.get(CONF_BATTERY_SOC_ENTITIES, [])
            current_capacities = self.data.get(CONF_BATTERY_CAPACITIES, {}) or {}
            battery_capacities = {}
            for entity_id in battery_entities:
                entity_key = f"capacity_{entity_id.replace('.', '_')}"
                if entity_key in user_input:
                    battery_capacities[entity_id] = user_input[entity_key]
                elif entity_id in current_capacities:
                    battery_capacities[entity_id] = current_capacities[entity_id]

            self.data[CONF_BATTERY_CAPACITIES] = battery_capacities
            if self.data.get(CONF_PHASE_MODE) == PHASE_MODE_THREE and battery_entities:
                return await self.async_step_battery_phase_assignment()
            return await self.async_step_settings()

        battery_entities = self.data.get(CONF_BATTERY_SOC_ENTITIES, [])
        if not battery_entities:
            self.data[CONF_BATTERY_CAPACITIES] = {}
            return await self.async_step_settings()

        current_capacities = self.data.get(CONF_BATTERY_CAPACITIES, {}) or {}
        schema_dict: dict[Any, Any] = {}
        for entity_id in battery_entities:
            entity_key = f"capacity_{entity_id.replace('.', '_')}"
            schema_dict[
                vol.Optional(
                    entity_key, default=current_capacities.get(entity_id, 10.0)
                )
            ] = selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=1.0,
                    max=200.0,
                    step=0.5,
                    unit_of_measurement="kWh",
                    mode=selector.NumberSelectorMode.BOX,
                )
            )

        return self.async_show_form(
            step_id="battery_capacities",
            data_schema=vol.Schema(schema_dict),
        )

    async def async_step_battery_phase_assignment(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Assign batteries to phases in options flow."""
        self._ensure_loaded()
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
                selected = user_input.get(key) or existing_assignments.get(entity_id)
                if not selected:
                    selected = [PHASE_IDS[0]]
                assignments[entity_id] = list(selected)
            self.data[CONF_BATTERY_PHASE_ASSIGNMENTS] = assignments
            return await self.async_step_settings()

        options = [
            {"value": phase_id, "label": DEFAULT_PHASE_NAMES[phase_id]}
            for phase_id in PHASE_IDS
        ]
        schema_dict: dict[Any, Any] = {}
        for entity_id in battery_entities:
            key = f"phase_assignment_{entity_id.replace('.', '_')}"
            schema_dict[
                vol.Optional(
                    key, default=existing_assignments.get(entity_id, [PHASE_IDS[0]])
                )
            ] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=options,
                    multiple=True,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )

        return self.async_show_form(
            step_id="battery_phase_assignment",
            data_schema=vol.Schema(schema_dict),
        )

    async def async_step_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure thresholds and strategy values."""
        self._ensure_loaded()
        if user_input is not None:
            self.data.update(user_input)
            return await self.async_step_safety_limits()

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_MIN_SOC_THRESHOLD,
                    default=self.data.get(CONF_MIN_SOC_THRESHOLD, DEFAULT_MIN_SOC),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=100, unit_of_measurement="%"
                    )
                ),
                vol.Optional(
                    CONF_MAX_SOC_THRESHOLD,
                    default=self.data.get(CONF_MAX_SOC_THRESHOLD, DEFAULT_MAX_SOC),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=100, unit_of_measurement="%"
                    )
                ),
                vol.Optional(
                    CONF_ARBITRAGE_MODE_RESERVE_SOC,
                    default=self.data.get(
                        CONF_ARBITRAGE_MODE_RESERVE_SOC,
                        DEFAULT_ARBITRAGE_MODE_RESERVE_SOC,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=100, unit_of_measurement="%"
                    )
                ),
                vol.Optional(
                    CONF_ARBITRAGE_MODE_RESERVE_SOC_SUNNY,
                    default=self.data.get(
                        CONF_ARBITRAGE_MODE_RESERVE_SOC_SUNNY,
                        DEFAULT_ARBITRAGE_MODE_RESERVE_SOC_SUNNY,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=100, unit_of_measurement="%"
                    )
                ),
                vol.Optional(
                    CONF_ARBITRAGE_MODE_DEADLINE_HOUR,
                    default=self.data.get(
                        CONF_ARBITRAGE_MODE_DEADLINE_HOUR,
                        DEFAULT_ARBITRAGE_MODE_DEADLINE_HOUR,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=23, step=1, unit_of_measurement="hour", mode="slider"
                    )
                ),
                vol.Optional(
                    CONF_NEGATIVE_BUY_THRESHOLD,
                    default=self.data.get(
                        CONF_NEGATIVE_BUY_THRESHOLD, DEFAULT_NEGATIVE_BUY_THRESHOLD
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=-1, max=1, step=0.01, unit_of_measurement="€/kWh"
                    )
                ),
                vol.Optional(
                    CONF_SOLAR_FORECAST_START_HOUR,
                    default=self.data.get(
                        CONF_SOLAR_FORECAST_START_HOUR,
                        DEFAULT_SOLAR_FORECAST_START_HOUR,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=12, max=23, step=1, mode="slider")
                ),
                vol.Optional(
                    CONF_MAX_SOC_THRESHOLD_SUNNY,
                    default=self.data.get(
                        CONF_MAX_SOC_THRESHOLD_SUNNY, DEFAULT_MAX_SOC_SUNNY
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=100, unit_of_measurement="%"
                    )
                ),
                vol.Optional(
                    CONF_MAX_SOC_THRESHOLD_SOLAR,
                    default=self.data.get(
                        CONF_MAX_SOC_THRESHOLD_SOLAR, DEFAULT_MAX_SOC_SOLAR
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=100, unit_of_measurement="%"
                    )
                ),
                vol.Optional(
                    CONF_SUNNY_FORECAST_THRESHOLD_KWH,
                    default=_default_sunny_forecast_threshold_kwh(self.data),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=100,
                        step=0.5,
                        unit_of_measurement="kWh",
                        mode="slider",
                    )
                ),
                vol.Optional(
                    CONF_PRICE_THRESHOLD,
                    default=self.data.get(
                        CONF_PRICE_THRESHOLD, DEFAULT_PRICE_THRESHOLD
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=1, step=0.01, unit_of_measurement="€/kWh"
                    )
                ),
                vol.Optional(
                    CONF_PRICE_ADJUSTMENT_MULTIPLIER,
                    default=self.data.get(
                        CONF_PRICE_ADJUSTMENT_MULTIPLIER,
                        DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0, max=5, step=0.01)
                ),
                vol.Optional(
                    CONF_PRICE_ADJUSTMENT_OFFSET,
                    default=self.data.get(
                        CONF_PRICE_ADJUSTMENT_OFFSET, DEFAULT_PRICE_ADJUSTMENT_OFFSET
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=-0.5, max=0.5, step=0.001, unit_of_measurement="€/kWh"
                    )
                ),
                vol.Optional(
                    CONF_EMERGENCY_SOC_THRESHOLD,
                    default=self.data.get(
                        CONF_EMERGENCY_SOC_THRESHOLD, DEFAULT_EMERGENCY_SOC
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=5, max=50, unit_of_measurement="%"
                    )
                ),
                vol.Optional(
                    CONF_SOC_BUFFER_TARGET,
                    default=self.data.get(
                        CONF_SOC_BUFFER_TARGET, DEFAULT_SOC_BUFFER_TARGET
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=30, max=80, unit_of_measurement="%"
                    )
                ),
                vol.Optional(
                    CONF_SOC_PRICE_MULTIPLIER_MAX,
                    default=self.data.get(
                        CONF_SOC_PRICE_MULTIPLIER_MAX, DEFAULT_SOC_PRICE_MULTIPLIER_MAX
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1.0, max=2.0, step=0.05)
                ),
                vol.Optional(
                    CONF_VERY_LOW_PRICE_THRESHOLD,
                    default=self.data.get(
                        CONF_VERY_LOW_PRICE_THRESHOLD, DEFAULT_VERY_LOW_PRICE_THRESHOLD
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=10, max=50, unit_of_measurement="%"
                    )
                ),
                vol.Optional(
                    CONF_SIGNIFICANT_SOLAR_THRESHOLD,
                    default=self.data.get(
                        CONF_SIGNIFICANT_SOLAR_THRESHOLD,
                        DEFAULT_SIGNIFICANT_SOLAR_THRESHOLD,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=500, max=5000, step=100, unit_of_measurement="W"
                    )
                ),
                vol.Optional(
                    CONF_FEEDIN_PRICE_THRESHOLD,
                    default=self.data.get(
                        CONF_FEEDIN_PRICE_THRESHOLD, DEFAULT_FEEDIN_PRICE_THRESHOLD
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0.0, max=1.0, step=0.01, unit_of_measurement="€/kWh"
                    )
                ),
                vol.Optional(
                    CONF_FEEDIN_ADJUSTMENT_MULTIPLIER,
                    default=self.data.get(
                        CONF_FEEDIN_ADJUSTMENT_MULTIPLIER,
                        DEFAULT_FEEDIN_ADJUSTMENT_MULTIPLIER,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=-1, max=5, step=0.01)
                ),
                vol.Optional(
                    CONF_FEEDIN_ADJUSTMENT_OFFSET,
                    default=self.data.get(
                        CONF_FEEDIN_ADJUSTMENT_OFFSET, DEFAULT_FEEDIN_ADJUSTMENT_OFFSET
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=-0.5, max=0.5, step=0.001, unit_of_measurement="€/kWh"
                    )
                ),
                vol.Optional(
                    CONF_BUY_VAT_MULTIPLIER,
                    default=self.data.get(
                        CONF_BUY_VAT_MULTIPLIER, DEFAULT_BUY_VAT_MULTIPLIER
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1.0, max=2.0, step=0.01)
                ),

                vol.Optional(
                    CONF_USE_DYNAMIC_THRESHOLD,
                    default=self.data.get(
                        CONF_USE_DYNAMIC_THRESHOLD, DEFAULT_USE_DYNAMIC_THRESHOLD
                    ),
                ): selector.BooleanSelector(),
                vol.Optional(
                    CONF_DYNAMIC_THRESHOLD_CONFIDENCE,
                    default=self.data.get(
                        CONF_DYNAMIC_THRESHOLD_CONFIDENCE,
                        DEFAULT_DYNAMIC_THRESHOLD_CONFIDENCE,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=30, max=90, step=5, unit_of_measurement="%"
                    )
                ),
                vol.Optional(
                    CONF_USE_AVERAGE_THRESHOLD,
                    default=self.data.get(
                        CONF_USE_AVERAGE_THRESHOLD, DEFAULT_USE_AVERAGE_THRESHOLD
                    ),
                ): selector.BooleanSelector(),
                vol.Optional(
                    CONF_MIN_CAR_CHARGING_DURATION,
                    default=self.data.get(
                        CONF_MIN_CAR_CHARGING_DURATION,
                        DEFAULT_MIN_CAR_CHARGING_DURATION,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1, max=6, step=1, unit_of_measurement="hours"
                    )
                ),
                vol.Optional(
                    CONF_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
                    default=self.data.get(
                        CONF_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
                        DEFAULT_CAR_PERMISSIVE_THRESHOLD_MULTIPLIER,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1.1, max=1.5, step=0.1, mode="slider"
                    )
                ),
                vol.Optional(
                    CONF_CAR_USE_BATTERY_ARBITRAGE,
                    default=self.data.get(
                        CONF_CAR_USE_BATTERY_ARBITRAGE,
                        DEFAULT_CAR_USE_BATTERY_ARBITRAGE,
                    ),
                ): selector.BooleanSelector(),
                vol.Optional(
                    CONF_MAX_INVERTER_POWER,
                    default=self.data.get(
                        CONF_MAX_INVERTER_POWER, DEFAULT_MAX_INVERTER_POWER
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=500, max=50000, step=100, unit_of_measurement="W"
                    )
                ),
                vol.Optional(
                    CONF_INVERTER_EXPORT_LIMIT,
                    default=self.data.get(
                        CONF_INVERTER_EXPORT_LIMIT, DEFAULT_INVERTER_EXPORT_LIMIT
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=5000, step=10, unit_of_measurement="W"
                    )
                ),
                vol.Optional(
                    CONF_INVERTER_EXPORT_DEADBAND,
                    default=self.data.get(
                        CONF_INVERTER_EXPORT_DEADBAND, DEFAULT_INVERTER_EXPORT_DEADBAND
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=500, step=5, unit_of_measurement="W"
                    )
                ),
                vol.Optional(
                    CONF_INVERTER_DERATING_UNUSED_RELEASE_MINUTES,
                    default=self.data.get(
                        CONF_INVERTER_DERATING_UNUSED_RELEASE_MINUTES,
                        DEFAULT_INVERTER_DERATING_UNUSED_RELEASE_MINUTES,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=120, step=1, unit_of_measurement="minutes"
                    )
                ),
                vol.Optional(
                    CONF_INVERTER_DERATING_SOC_BYPASS_THRESHOLD,
                    default=self.data.get(
                        CONF_INVERTER_DERATING_SOC_BYPASS_THRESHOLD,
                        DEFAULT_INVERTER_DERATING_SOC_BYPASS_THRESHOLD,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=100, step=1, unit_of_measurement="%"
                    )
                ),
            }
        )
        return self.async_show_form(step_id="settings", data_schema=schema)

    async def async_step_safety_limits(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure safety limits and finish options flow."""
        self._ensure_loaded()
        errors: dict[str, str] = {}
        validation_error_text = ""
        if user_input is not None:
            self.data.update(user_input)
            self.data = normalize_entity_values(self.data)
            validation_errors = validate_config_consistency(self.data)
            if validation_errors:
                errors["base"] = "invalid_config"
                validation_error_text = "; ".join(validation_errors)
            else:
                return self.async_create_entry(title="", data=self.data)

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_MAX_BATTERY_POWER,
                    default=self.data.get(
                        CONF_MAX_BATTERY_POWER, DEFAULT_MAX_BATTERY_POWER
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1000, max=10000, step=500, unit_of_measurement="W"
                    )
                ),
                vol.Optional(
                    CONF_MAX_CAR_POWER,
                    default=self.data.get(CONF_MAX_CAR_POWER, DEFAULT_MAX_CAR_POWER),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1000, max=22000, step=1000, unit_of_measurement="W"
                    )
                ),
                vol.Optional(
                    CONF_MAX_GRID_POWER,
                    default=self.data.get(CONF_MAX_GRID_POWER, DEFAULT_MAX_GRID_POWER),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=3000, max=30000, step=1000, unit_of_measurement="W"
                    )
                ),
                vol.Optional(
                    CONF_ARBITRAGE_MODE_MAX_EXPORT_POWER,
                    default=self.data.get(
                        CONF_ARBITRAGE_MODE_MAX_EXPORT_POWER,
                        DEFAULT_ARBITRAGE_MODE_MAX_EXPORT_POWER,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=50000, step=100, unit_of_measurement="W"
                    )
                ),
                vol.Optional(
                    CONF_MIN_CAR_CHARGING_THRESHOLD,
                    default=self.data.get(
                        CONF_MIN_CAR_CHARGING_THRESHOLD,
                        DEFAULT_MIN_CAR_CHARGING_THRESHOLD,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=50, max=500, step=50, unit_of_measurement="W"
                    )
                ),
                vol.Optional(
                    CONF_PREDICTIVE_CHARGING_MIN_SOC,
                    default=self.data.get(
                        CONF_PREDICTIVE_CHARGING_MIN_SOC,
                        DEFAULT_PREDICTIVE_CHARGING_MIN_SOC,
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=20, max=60, unit_of_measurement="%"
                    )
                ),
                vol.Optional(
                    CONF_BASE_GRID_SETPOINT,
                    default=self.data.get(
                        CONF_BASE_GRID_SETPOINT, DEFAULT_BASE_GRID_SETPOINT
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1000, max=10000, step=100, unit_of_measurement="W"
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="safety_limits",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "validation_errors": (
                    f"Validation error: {validation_error_text}"
                    if validation_error_text
                    else ""
                ),
            },
        )
