"""Config flow tests for Electricity Planner."""
from __future__ import annotations

import pytest
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.electricity_planner.config_flow import OptionsFlowHandler
from custom_components.electricity_planner.const import (
    CONF_BATTERY_CAPACITIES,
    CONF_BATTERY_PHASE_ASSIGNMENTS,
    CONF_BATTERY_SOC_ENTITIES,
    CONF_BASE_GRID_SETPOINT,
    CONF_DYNAMIC_THRESHOLD_CONFIDENCE,
    CONF_EMERGENCY_SOC_THRESHOLD,
    CONF_INVERTER_DERATING_UNUSED_RELEASE_MINUTES,
    CONF_INVERTER_EXPORT_DEADBAND,
    CONF_INVERTER_EXPORT_LIMIT,
    CONF_FEEDIN_ADJUSTMENT_MULTIPLIER,
    CONF_FEEDIN_ADJUSTMENT_OFFSET,
    CONF_FEEDIN_PRICE_THRESHOLD,
    CONF_CURRENT_PRICE_ENTITY,
    CONF_HIGHEST_PRICE_ENTITY,
    CONF_LOWEST_PRICE_ENTITY,
    CONF_NEXT_PRICE_ENTITY,
    CONF_SOLAR_PRODUCTION_ENTITY,
    CONF_HOUSE_CONSUMPTION_ENTITY,
    CONF_CAR_CHARGING_POWER_ENTITY,
    CONF_PHASE_MODE,
    CONF_PHASE_NAME,
    CONF_PHASES,
    CONF_PHASE_SOLAR_ENTITY,
    CONF_MAX_BATTERY_POWER,
    CONF_MAX_CAR_POWER,
    CONF_MAX_GRID_POWER,
    CONF_MIN_CAR_CHARGING_DURATION,
    CONF_MIN_CAR_CHARGING_THRESHOLD,
    CONF_MIN_SOC_THRESHOLD,
    CONF_PREDICTIVE_CHARGING_MIN_SOC,
    CONF_PRICE_ADJUSTMENT_MULTIPLIER,
    CONF_PRICE_ADJUSTMENT_OFFSET,
    CONF_PRICE_THRESHOLD,
    CONF_SIGNIFICANT_SOLAR_THRESHOLD,
    CONF_SUNNY_FORECAST_THRESHOLD_KWH,
    CONF_USE_AVERAGE_THRESHOLD,
    CONF_USE_DYNAMIC_THRESHOLD,
    CONF_VERY_LOW_PRICE_THRESHOLD,
    CONF_MAX_SOC_THRESHOLD,
    CONF_MAX_SOC_THRESHOLD_SUNNY,
    DEFAULT_BASE_GRID_SETPOINT,
    DEFAULT_DYNAMIC_THRESHOLD_CONFIDENCE,
    DEFAULT_EMERGENCY_SOC,
    DEFAULT_INVERTER_DERATING_UNUSED_RELEASE_MINUTES,
    DEFAULT_INVERTER_EXPORT_DEADBAND,
    DEFAULT_INVERTER_EXPORT_LIMIT,
    DEFAULT_FEEDIN_ADJUSTMENT_MULTIPLIER,
    DEFAULT_FEEDIN_ADJUSTMENT_OFFSET,
    DEFAULT_FEEDIN_PRICE_THRESHOLD,
    DEFAULT_MAX_BATTERY_POWER,
    DEFAULT_MAX_CAR_POWER,
    DEFAULT_MAX_GRID_POWER,
    DEFAULT_MIN_CAR_CHARGING_DURATION,
    DEFAULT_MIN_CAR_CHARGING_THRESHOLD,
    DEFAULT_MIN_SOC,
    DEFAULT_PREDICTIVE_CHARGING_MIN_SOC,
    DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER,
    DEFAULT_PRICE_ADJUSTMENT_OFFSET,
    DEFAULT_PRICE_THRESHOLD,
    DEFAULT_SIGNIFICANT_SOLAR_THRESHOLD,
    DEFAULT_USE_AVERAGE_THRESHOLD,
    DEFAULT_USE_DYNAMIC_THRESHOLD,
    DEFAULT_VERY_LOW_PRICE_THRESHOLD,
    DEFAULT_MAX_SOC,
    PHASE_MODE_SINGLE,
    PHASE_MODE_THREE,
    DOMAIN,
)


@pytest.mark.asyncio
async def test_options_flow_returns_updated_options():
    entry = MockConfigEntry(domain=DOMAIN, data={})
    handler = OptionsFlowHandler()
    object.__setattr__(handler, 'config_entry', entry)  # Bypass read-only property for testing

    battery_entity = "sensor.main_battery"

    user_input = {
        CONF_MIN_SOC_THRESHOLD: DEFAULT_MIN_SOC,
        CONF_MAX_SOC_THRESHOLD: DEFAULT_MAX_SOC,
        CONF_PRICE_THRESHOLD: 0.123,
        CONF_PRICE_ADJUSTMENT_MULTIPLIER: DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER,
        CONF_PRICE_ADJUSTMENT_OFFSET: DEFAULT_PRICE_ADJUSTMENT_OFFSET,
        CONF_EMERGENCY_SOC_THRESHOLD: DEFAULT_EMERGENCY_SOC,
        CONF_VERY_LOW_PRICE_THRESHOLD: DEFAULT_VERY_LOW_PRICE_THRESHOLD,
        CONF_SIGNIFICANT_SOLAR_THRESHOLD: DEFAULT_SIGNIFICANT_SOLAR_THRESHOLD,
        CONF_SUNNY_FORECAST_THRESHOLD_KWH: 7.5,
        CONF_FEEDIN_PRICE_THRESHOLD: DEFAULT_FEEDIN_PRICE_THRESHOLD,
        CONF_FEEDIN_ADJUSTMENT_MULTIPLIER: DEFAULT_FEEDIN_ADJUSTMENT_MULTIPLIER,
        CONF_FEEDIN_ADJUSTMENT_OFFSET: DEFAULT_FEEDIN_ADJUSTMENT_OFFSET,
        CONF_USE_DYNAMIC_THRESHOLD: not DEFAULT_USE_DYNAMIC_THRESHOLD,
        CONF_DYNAMIC_THRESHOLD_CONFIDENCE: DEFAULT_DYNAMIC_THRESHOLD_CONFIDENCE,
        CONF_USE_AVERAGE_THRESHOLD: DEFAULT_USE_AVERAGE_THRESHOLD,
        CONF_MIN_CAR_CHARGING_DURATION: DEFAULT_MIN_CAR_CHARGING_DURATION,
        CONF_MAX_BATTERY_POWER: DEFAULT_MAX_BATTERY_POWER,
        CONF_MAX_CAR_POWER: DEFAULT_MAX_CAR_POWER,
        CONF_MAX_GRID_POWER: DEFAULT_MAX_GRID_POWER,
        CONF_MIN_CAR_CHARGING_THRESHOLD: DEFAULT_MIN_CAR_CHARGING_THRESHOLD,
        CONF_PREDICTIVE_CHARGING_MIN_SOC: DEFAULT_PREDICTIVE_CHARGING_MIN_SOC,
        CONF_BASE_GRID_SETPOINT: DEFAULT_BASE_GRID_SETPOINT,
        CONF_INVERTER_EXPORT_LIMIT: 120,
        CONF_INVERTER_EXPORT_DEADBAND: 35,
        CONF_INVERTER_DERATING_UNUSED_RELEASE_MINUTES: 7,
        CONF_BATTERY_SOC_ENTITIES: [battery_entity],
        "capacity_sensor_main_battery": 12.5,
    }

    result = await handler.async_step_init(user_input)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PRICE_THRESHOLD] == 0.123
    assert result["data"][CONF_USE_DYNAMIC_THRESHOLD] is (not DEFAULT_USE_DYNAMIC_THRESHOLD)
    assert result["data"][CONF_SUNNY_FORECAST_THRESHOLD_KWH] == pytest.approx(7.5)
    assert result["data"][CONF_INVERTER_EXPORT_LIMIT] == 120
    assert result["data"][CONF_INVERTER_EXPORT_DEADBAND] == 35
    assert result["data"][CONF_INVERTER_DERATING_UNUSED_RELEASE_MINUTES] == 7
    assert result["data"][CONF_BATTERY_CAPACITIES] == {battery_entity: 12.5}
    assert "capacity_sensor_main_battery" not in result["data"]
    # Options flow should not have mutated the original entry data
    assert entry.data == {}


@pytest.mark.asyncio
async def test_options_flow_defaults_reflect_existing_options():
    battery_entity = "sensor.main_battery"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
            CONF_HIGHEST_PRICE_ENTITY: "sensor.highest_price",
            CONF_LOWEST_PRICE_ENTITY: "sensor.lowest_price",
            CONF_NEXT_PRICE_ENTITY: "sensor.next_price",
            CONF_BATTERY_SOC_ENTITIES: [battery_entity],
            CONF_SOLAR_PRODUCTION_ENTITY: "sensor.solar_production",
            CONF_HOUSE_CONSUMPTION_ENTITY: "sensor.house_consumption",
        },
        options={
            CONF_PRICE_THRESHOLD: 0.321,
            CONF_BATTERY_SOC_ENTITIES: [battery_entity],
            CONF_BATTERY_CAPACITIES: {battery_entity: 11.5},
        },
    )

    handler = OptionsFlowHandler()
    object.__setattr__(handler, 'config_entry', entry)  # Bypass read-only property for testing
    result = await handler.async_step_init()

    assert result["type"] == FlowResultType.FORM
    schema = result["data_schema"]

    def default_for(field_name: str):
        key = next(
            key for key in schema.schema if getattr(key, "schema", None) == field_name
        )
        default = getattr(key, "default", None)
        return default() if callable(default) else default

    assert default_for(CONF_PRICE_THRESHOLD) == pytest.approx(0.321)
    assert default_for(CONF_SUNNY_FORECAST_THRESHOLD_KWH) == pytest.approx(5.8)
    assert default_for(CONF_INVERTER_EXPORT_LIMIT) == DEFAULT_INVERTER_EXPORT_LIMIT
    assert default_for(CONF_INVERTER_EXPORT_DEADBAND) == DEFAULT_INVERTER_EXPORT_DEADBAND
    assert (
        default_for(CONF_INVERTER_DERATING_UNUSED_RELEASE_MINUTES)
        == DEFAULT_INVERTER_DERATING_UNUSED_RELEASE_MINUTES
    )

    capacity_field = f"capacity_{battery_entity.replace('.', '_')}"
    assert default_for(capacity_field) == pytest.approx(11.5)


@pytest.mark.asyncio
async def test_options_flow_derives_sunny_forecast_threshold_from_capacity():
    battery_entity = "sensor.main_battery"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
            CONF_HIGHEST_PRICE_ENTITY: "sensor.highest_price",
            CONF_LOWEST_PRICE_ENTITY: "sensor.lowest_price",
            CONF_NEXT_PRICE_ENTITY: "sensor.next_price",
            CONF_BATTERY_SOC_ENTITIES: [battery_entity],
        },
        options={
            CONF_BATTERY_SOC_ENTITIES: [battery_entity],
            CONF_BATTERY_CAPACITIES: {battery_entity: 14.0},
        },
    )

    handler = OptionsFlowHandler()
    object.__setattr__(handler, "config_entry", entry)
    result = await handler.async_step_init()

    schema = result["data_schema"]
    key = next(
        key
        for key in schema.schema
        if getattr(key, "schema", None) == CONF_SUNNY_FORECAST_THRESHOLD_KWH
    )
    default = key.default() if callable(key.default) else key.default
    assert default == pytest.approx(7.0)


@pytest.mark.asyncio
async def test_options_flow_preserves_existing_soc_thresholds_when_payload_is_partial():
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
            CONF_MAX_SOC_THRESHOLD: 90,
            CONF_MAX_SOC_THRESHOLD_SUNNY: 50,
        },
        options={
            CONF_MAX_SOC_THRESHOLD: 82,
            CONF_MAX_SOC_THRESHOLD_SUNNY: 38,
            CONF_SUNNY_FORECAST_THRESHOLD_KWH: 6.5,
        },
    )

    handler = OptionsFlowHandler()
    object.__setattr__(handler, "config_entry", entry)

    result = await handler.async_step_init(
        {
            CONF_PRICE_THRESHOLD: 0.19,
        }
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PRICE_THRESHOLD] == pytest.approx(0.19)
    assert result["data"][CONF_MAX_SOC_THRESHOLD] == 82
    assert result["data"][CONF_MAX_SOC_THRESHOLD_SUNNY] == 38
    assert result["data"][CONF_SUNNY_FORECAST_THRESHOLD_KWH] == pytest.approx(6.5)


@pytest.mark.asyncio
async def test_options_flow_preserves_three_phase_dynamic_fields_when_payload_is_partial():
    battery_entity = "sensor.main_battery"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
        },
        options={
            CONF_PHASE_MODE: PHASE_MODE_THREE,
            CONF_BATTERY_SOC_ENTITIES: [battery_entity],
            CONF_BATTERY_CAPACITIES: {battery_entity: 11.5},
            CONF_BATTERY_PHASE_ASSIGNMENTS: {battery_entity: ["phase_2"]},
            CONF_PHASES: {
                "phase_1": {CONF_PHASE_SOLAR_ENTITY: "sensor.phase_1_solar"},
            },
        },
    )

    handler = OptionsFlowHandler()
    object.__setattr__(handler, "config_entry", entry)

    result = await handler.async_step_init(
        {
            CONF_PHASE_MODE: PHASE_MODE_THREE,
            CONF_PRICE_THRESHOLD: 0.19,
        }
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_BATTERY_CAPACITIES] == {battery_entity: 11.5}
    assert result["data"][CONF_BATTERY_PHASE_ASSIGNMENTS] == {
        battery_entity: ["phase_2"]
    }
    assert result["data"][CONF_PHASES] == {
        "phase_1": {
            CONF_PHASE_NAME: "Phase 1",
            CONF_PHASE_SOLAR_ENTITY: "sensor.phase_1_solar",
        }
    }


@pytest.mark.asyncio
async def test_options_flow_hides_phase_assignment_when_single_phase():
    battery_entity = "sensor.main_battery"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_BATTERY_SOC_ENTITIES: [battery_entity],
        },
        options={
            CONF_BATTERY_SOC_ENTITIES: [battery_entity],
            CONF_BATTERY_CAPACITIES: {battery_entity: 8.0},
            # Explicitly ensure stored phase mode is single
            CONF_PHASE_MODE: PHASE_MODE_SINGLE,
        },
    )

    handler = OptionsFlowHandler()
    object.__setattr__(handler, 'config_entry', entry)  # Bypass read-only property for testing
    result = await handler.async_step_init()

    assert result["type"] == FlowResultType.FORM
    schema = result["data_schema"].schema

    phase_assignment_field = f"phase_assignment_{battery_entity.replace('.', '_')}"
    assert not any(getattr(field, "schema", None) == phase_assignment_field for field in schema)


@pytest.mark.asyncio
async def test_options_flow_rejects_invalid_threshold_combination():
    entry = MockConfigEntry(domain=DOMAIN, data={})
    handler = OptionsFlowHandler()
    object.__setattr__(handler, "config_entry", entry)

    result = await handler.async_step_init(
        {
            CONF_MIN_SOC_THRESHOLD: 60,
            CONF_MAX_SOC_THRESHOLD: 40,
            CONF_MAX_BATTERY_POWER: DEFAULT_MAX_BATTERY_POWER,
            CONF_MAX_CAR_POWER: DEFAULT_MAX_CAR_POWER,
            CONF_MAX_GRID_POWER: DEFAULT_MAX_GRID_POWER,
        }
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_config"
    assert "min_soc" in result["description_placeholders"]["validation_errors"]


@pytest.mark.asyncio
async def test_options_flow_rejects_sunny_soc_above_normal_max_soc():
    entry = MockConfigEntry(domain=DOMAIN, data={})
    handler = OptionsFlowHandler()
    object.__setattr__(handler, "config_entry", entry)

    result = await handler.async_step_init(
        {
            CONF_MAX_SOC_THRESHOLD: 40,
            CONF_MAX_SOC_THRESHOLD_SUNNY: 70,
        }
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_config"
    assert "max_soc_threshold_sunny" in result["description_placeholders"]["validation_errors"]


@pytest.mark.asyncio
async def test_options_flow_switch_to_three_phase_preserves_single_phase_bindings():
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_SOLAR_PRODUCTION_ENTITY: "sensor.solar",
            CONF_HOUSE_CONSUMPTION_ENTITY: "sensor.house",
        },
        options={
            CONF_CAR_CHARGING_POWER_ENTITY: "sensor.car",
            CONF_PHASE_MODE: PHASE_MODE_SINGLE,
        },
    )
    handler = OptionsFlowHandler()
    object.__setattr__(handler, "config_entry", entry)

    result = await handler.async_step_init({CONF_PHASE_MODE: PHASE_MODE_THREE})

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PHASE_MODE] == PHASE_MODE_THREE
    assert result["data"][CONF_SOLAR_PRODUCTION_ENTITY] == "sensor.solar"
    assert result["data"][CONF_HOUSE_CONSUMPTION_ENTITY] == "sensor.house"
    assert result["data"][CONF_CAR_CHARGING_POWER_ENTITY] == "sensor.car"


@pytest.mark.asyncio
async def test_options_flow_can_clear_existing_phase_entities():
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={
            CONF_PHASE_MODE: PHASE_MODE_THREE,
            CONF_PHASES: {
                "phase_1": {CONF_PHASE_SOLAR_ENTITY: "sensor.phase_1_solar"},
            },
            CONF_BATTERY_PHASE_ASSIGNMENTS: {},
        },
    )
    handler = OptionsFlowHandler()
    object.__setattr__(handler, "config_entry", entry)

    result = await handler.async_step_init(
        {
            CONF_PHASE_MODE: PHASE_MODE_THREE,
            "phase_1_solar_entity": "",
        }
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PHASE_MODE] == PHASE_MODE_THREE
    assert result["data"][CONF_PHASES] == {}
