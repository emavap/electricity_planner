"""Config flow tests for Electricity Planner."""
from __future__ import annotations

import pytest
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.electricity_planner.config_flow import (
    OptionsFlowHandler,
    validate_config_consistency,
)
from custom_components.electricity_planner.const import (
    CONF_ARBITRAGE_MODE_DEADLINE_HOUR,
    CONF_ARBITRAGE_MODE_MAX_EXPORT_POWER,
    CONF_ARBITRAGE_MODE_RESERVE_SOC,
    CONF_BATTERY_CAPACITIES,
    CONF_BATTERY_PHASE_ASSIGNMENTS,
    CONF_BATTERY_SOC_ENTITIES,
    CONF_BASE_GRID_SETPOINT,
    CONF_CAR_USE_BATTERY_ARBITRAGE,
    CONF_CURRENT_PRICE_ENTITY,
    CONF_DYNAMIC_THRESHOLD_CONFIDENCE,
    CONF_EMERGENCY_SOC_THRESHOLD,
    CONF_HIGHEST_PRICE_ENTITY,
    CONF_HOUSE_CONSUMPTION_ENTITY,
    CONF_INVERTER_DERATING_UNUSED_RELEASE_MINUTES,
    CONF_INVERTER_EXPORT_DEADBAND,
    CONF_INVERTER_EXPORT_LIMIT,
    CONF_LOWEST_PRICE_ENTITY,
    CONF_MAX_BATTERY_POWER,
    CONF_MAX_CAR_POWER,
    CONF_MAX_GRID_POWER,
    CONF_MAX_SOC_THRESHOLD,
    CONF_MAX_SOC_THRESHOLD_SUNNY,
    CONF_MIN_CAR_CHARGING_DURATION,
    CONF_MIN_CAR_CHARGING_THRESHOLD,
    CONF_MIN_SOC_THRESHOLD,
    CONF_NEXT_PRICE_ENTITY,
    CONF_PHASE_MODE,
    CONF_PHASE_NAME,
    CONF_PHASES,
    CONF_PHASE_SOLAR_ENTITY,
    CONF_PREDICTIVE_CHARGING_MIN_SOC,
    CONF_PRICE_ADJUSTMENT_MULTIPLIER,
    CONF_PRICE_ADJUSTMENT_OFFSET,
    CONF_PRICE_THRESHOLD,
    CONF_SIGNIFICANT_SOLAR_THRESHOLD,
    CONF_SOLAR_PRODUCTION_ENTITY,
    CONF_SOLAR_FORECAST_START_HOUR,
    CONF_SUNNY_FORECAST_THRESHOLD_KWH,
    CONF_USE_AVERAGE_THRESHOLD,
    CONF_USE_DYNAMIC_THRESHOLD,
    CONF_VERY_LOW_PRICE_THRESHOLD,
    DEFAULT_ARBITRAGE_MODE_DEADLINE_HOUR,
    DEFAULT_ARBITRAGE_MODE_MAX_EXPORT_POWER,
    DEFAULT_ARBITRAGE_MODE_RESERVE_SOC,
    DEFAULT_BASE_GRID_SETPOINT,
    DEFAULT_CAR_USE_BATTERY_ARBITRAGE,
    DEFAULT_DYNAMIC_THRESHOLD_CONFIDENCE,
    DEFAULT_EMERGENCY_SOC,
    DEFAULT_INVERTER_DERATING_UNUSED_RELEASE_MINUTES,
    DEFAULT_INVERTER_EXPORT_DEADBAND,
    DEFAULT_INVERTER_EXPORT_LIMIT,
    DEFAULT_MAX_BATTERY_POWER,
    DEFAULT_MAX_CAR_POWER,
    DEFAULT_MAX_GRID_POWER,
    DEFAULT_MAX_SOC,
    DEFAULT_MIN_CAR_CHARGING_DURATION,
    DEFAULT_MIN_CAR_CHARGING_THRESHOLD,
    DEFAULT_MIN_SOC,
    DEFAULT_PREDICTIVE_CHARGING_MIN_SOC,
    DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER,
    DEFAULT_PRICE_ADJUSTMENT_OFFSET,
    DEFAULT_USE_AVERAGE_THRESHOLD,
    DEFAULT_USE_DYNAMIC_THRESHOLD,
    DOMAIN,
    PHASE_MODE_SINGLE,
    PHASE_MODE_THREE,
)


def _make_handler(entry: MockConfigEntry) -> OptionsFlowHandler:
    handler = OptionsFlowHandler()
    object.__setattr__(handler, "config_entry", entry)
    return handler


def _default_for(schema, field_name: str):
    key = next(key for key in schema.schema if getattr(key, "schema", None) == field_name)
    default = getattr(key, "default", None)
    return default() if callable(default) else default


async def _go_to_entities(handler: OptionsFlowHandler, phase_mode: str = PHASE_MODE_SINGLE):
    result = await handler.async_step_init()
    assert result["type"] == FlowResultType.FORM
    result = await handler.async_step_init({CONF_PHASE_MODE: phase_mode})
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "entities"
    return result


async def _go_to_settings(
    handler: OptionsFlowHandler,
    entities_input: dict,
    *,
    phase_mode: str = PHASE_MODE_SINGLE,
):
    await _go_to_entities(handler, phase_mode=phase_mode)
    result = await handler.async_step_entities(entities_input)
    if result["type"] == FlowResultType.FORM and result["step_id"] == "battery_capacities":
        capacities_input = {
            getattr(key, "schema", ""): _default_for(result["data_schema"], getattr(key, "schema", ""))
            for key in result["data_schema"].schema
        }
        result = await handler.async_step_battery_capacities(capacities_input)
    if result["type"] == FlowResultType.FORM and result["step_id"] == "battery_phase_assignment":
        phase_input = {}
        for entity_id in entities_input.get(CONF_BATTERY_SOC_ENTITIES, []):
            phase_input[f"phase_assignment_{entity_id.replace('.', '_')}"] = ["phase_1"]
        result = await handler.async_step_battery_phase_assignment(phase_input)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "settings"
    return result


@pytest.mark.asyncio
async def test_options_flow_is_multistep_and_returns_updated_options():
    entry = MockConfigEntry(domain=DOMAIN, data={})
    handler = _make_handler(entry)
    battery_entity = "sensor.main_battery"

    entities_input = {
        CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
        CONF_HIGHEST_PRICE_ENTITY: "sensor.highest_price",
        CONF_LOWEST_PRICE_ENTITY: "sensor.lowest_price",
        CONF_NEXT_PRICE_ENTITY: "sensor.next_price",
        CONF_BATTERY_SOC_ENTITIES: [battery_entity],
    }
    settings_input = {
        CONF_MIN_SOC_THRESHOLD: DEFAULT_MIN_SOC,
        CONF_MAX_SOC_THRESHOLD: DEFAULT_MAX_SOC,
        CONF_ARBITRAGE_MODE_RESERVE_SOC: 25,
        CONF_ARBITRAGE_MODE_DEADLINE_HOUR: 9,
        CONF_PRICE_THRESHOLD: 0.123,
        CONF_PRICE_ADJUSTMENT_MULTIPLIER: DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER,
        CONF_PRICE_ADJUSTMENT_OFFSET: DEFAULT_PRICE_ADJUSTMENT_OFFSET,
        CONF_EMERGENCY_SOC_THRESHOLD: DEFAULT_EMERGENCY_SOC,
        CONF_VERY_LOW_PRICE_THRESHOLD: 30,
        CONF_SIGNIFICANT_SOLAR_THRESHOLD: 1200,
        CONF_SUNNY_FORECAST_THRESHOLD_KWH: 7.5,
        CONF_USE_DYNAMIC_THRESHOLD: not DEFAULT_USE_DYNAMIC_THRESHOLD,
        CONF_DYNAMIC_THRESHOLD_CONFIDENCE: DEFAULT_DYNAMIC_THRESHOLD_CONFIDENCE,
        CONF_USE_AVERAGE_THRESHOLD: DEFAULT_USE_AVERAGE_THRESHOLD,
        CONF_MIN_CAR_CHARGING_DURATION: DEFAULT_MIN_CAR_CHARGING_DURATION,
        CONF_CAR_USE_BATTERY_ARBITRAGE: False,
        CONF_MAX_SOC_THRESHOLD_SUNNY: 50,
        CONF_INVERTER_EXPORT_LIMIT: 120,
        CONF_INVERTER_EXPORT_DEADBAND: 35,
        CONF_INVERTER_DERATING_UNUSED_RELEASE_MINUTES: 7,
    }
    safety_input = {
        CONF_MAX_BATTERY_POWER: DEFAULT_MAX_BATTERY_POWER,
        CONF_MAX_CAR_POWER: DEFAULT_MAX_CAR_POWER,
        CONF_MAX_GRID_POWER: DEFAULT_MAX_GRID_POWER,
        CONF_ARBITRAGE_MODE_MAX_EXPORT_POWER: 4500,
        CONF_MIN_CAR_CHARGING_THRESHOLD: DEFAULT_MIN_CAR_CHARGING_THRESHOLD,
        CONF_PREDICTIVE_CHARGING_MIN_SOC: DEFAULT_PREDICTIVE_CHARGING_MIN_SOC,
        CONF_BASE_GRID_SETPOINT: DEFAULT_BASE_GRID_SETPOINT,
    }

    await _go_to_entities(handler)
    result = await handler.async_step_entities(entities_input)
    assert result["step_id"] == "battery_capacities"
    result = await handler.async_step_battery_capacities(
        {"capacity_sensor_main_battery": 12.5}
    )
    assert result["step_id"] == "settings"
    result = await handler.async_step_settings(settings_input)
    assert result["step_id"] == "safety_limits"
    result = await handler.async_step_safety_limits(safety_input)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PRICE_THRESHOLD] == pytest.approx(0.123)
    assert result["data"][CONF_ARBITRAGE_MODE_RESERVE_SOC] == 25
    assert result["data"][CONF_ARBITRAGE_MODE_DEADLINE_HOUR] == 9
    assert result["data"][CONF_USE_DYNAMIC_THRESHOLD] is True
    assert result["data"][CONF_CAR_USE_BATTERY_ARBITRAGE] is False
    assert result["data"][CONF_SUNNY_FORECAST_THRESHOLD_KWH] == pytest.approx(7.5)
    assert result["data"][CONF_INVERTER_EXPORT_LIMIT] == 120
    assert result["data"][CONF_INVERTER_EXPORT_DEADBAND] == 35
    assert result["data"][CONF_INVERTER_DERATING_UNUSED_RELEASE_MINUTES] == 7
    assert result["data"][CONF_ARBITRAGE_MODE_MAX_EXPORT_POWER] == 4500
    assert result["data"][CONF_BATTERY_CAPACITIES] == {battery_entity: 12.5}


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
            CONF_ARBITRAGE_MODE_RESERVE_SOC: 27,
            CONF_ARBITRAGE_MODE_MAX_EXPORT_POWER: 4200,
            CONF_BATTERY_SOC_ENTITIES: [battery_entity],
            CONF_BATTERY_CAPACITIES: {battery_entity: 11.5},
        },
    )
    handler = _make_handler(entry)

    result = await _go_to_settings(
        handler,
        {
            CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
            CONF_HIGHEST_PRICE_ENTITY: "sensor.highest_price",
            CONF_LOWEST_PRICE_ENTITY: "sensor.lowest_price",
            CONF_NEXT_PRICE_ENTITY: "sensor.next_price",
            CONF_BATTERY_SOC_ENTITIES: [battery_entity],
            CONF_SOLAR_PRODUCTION_ENTITY: "sensor.solar_production",
            CONF_HOUSE_CONSUMPTION_ENTITY: "sensor.house_consumption",
        },
    )

    schema = result["data_schema"]
    assert _default_for(schema, CONF_PRICE_THRESHOLD) == pytest.approx(0.321)
    assert _default_for(schema, CONF_ARBITRAGE_MODE_RESERVE_SOC) == pytest.approx(27)
    assert _default_for(schema, CONF_SUNNY_FORECAST_THRESHOLD_KWH) == pytest.approx(5.8)
    assert _default_for(schema, CONF_INVERTER_EXPORT_LIMIT) == DEFAULT_INVERTER_EXPORT_LIMIT
    assert _default_for(schema, CONF_INVERTER_EXPORT_DEADBAND) == DEFAULT_INVERTER_EXPORT_DEADBAND
    assert _default_for(schema, CONF_CAR_USE_BATTERY_ARBITRAGE) == DEFAULT_CAR_USE_BATTERY_ARBITRAGE
    assert (
        _default_for(schema, CONF_INVERTER_DERATING_UNUSED_RELEASE_MINUTES)
        == DEFAULT_INVERTER_DERATING_UNUSED_RELEASE_MINUTES
    )

    safety_result = await handler.async_step_settings(
        {
            CONF_MIN_SOC_THRESHOLD: _default_for(schema, CONF_MIN_SOC_THRESHOLD),
            CONF_MAX_SOC_THRESHOLD: _default_for(schema, CONF_MAX_SOC_THRESHOLD),
            CONF_ARBITRAGE_MODE_RESERVE_SOC: _default_for(schema, CONF_ARBITRAGE_MODE_RESERVE_SOC),
            CONF_ARBITRAGE_MODE_DEADLINE_HOUR: _default_for(schema, CONF_ARBITRAGE_MODE_DEADLINE_HOUR),
            CONF_SOLAR_FORECAST_START_HOUR: _default_for(schema, CONF_SOLAR_FORECAST_START_HOUR),
            CONF_MAX_SOC_THRESHOLD_SUNNY: _default_for(schema, CONF_MAX_SOC_THRESHOLD_SUNNY),
            CONF_SUNNY_FORECAST_THRESHOLD_KWH: _default_for(schema, CONF_SUNNY_FORECAST_THRESHOLD_KWH),
            CONF_PRICE_THRESHOLD: _default_for(schema, CONF_PRICE_THRESHOLD),
            CONF_PRICE_ADJUSTMENT_MULTIPLIER: _default_for(schema, CONF_PRICE_ADJUSTMENT_MULTIPLIER),
            CONF_PRICE_ADJUSTMENT_OFFSET: _default_for(schema, CONF_PRICE_ADJUSTMENT_OFFSET),
            CONF_EMERGENCY_SOC_THRESHOLD: _default_for(schema, CONF_EMERGENCY_SOC_THRESHOLD),
            CONF_VERY_LOW_PRICE_THRESHOLD: _default_for(schema, CONF_VERY_LOW_PRICE_THRESHOLD),
            CONF_SIGNIFICANT_SOLAR_THRESHOLD: _default_for(schema, CONF_SIGNIFICANT_SOLAR_THRESHOLD),
            CONF_USE_DYNAMIC_THRESHOLD: _default_for(schema, CONF_USE_DYNAMIC_THRESHOLD),
            CONF_DYNAMIC_THRESHOLD_CONFIDENCE: _default_for(schema, CONF_DYNAMIC_THRESHOLD_CONFIDENCE),
            CONF_USE_AVERAGE_THRESHOLD: _default_for(schema, CONF_USE_AVERAGE_THRESHOLD),
            CONF_MIN_CAR_CHARGING_DURATION: _default_for(schema, CONF_MIN_CAR_CHARGING_DURATION),
            CONF_CAR_USE_BATTERY_ARBITRAGE: _default_for(schema, CONF_CAR_USE_BATTERY_ARBITRAGE),
            CONF_INVERTER_EXPORT_LIMIT: _default_for(schema, CONF_INVERTER_EXPORT_LIMIT),
            CONF_INVERTER_EXPORT_DEADBAND: _default_for(schema, CONF_INVERTER_EXPORT_DEADBAND),
            CONF_INVERTER_DERATING_UNUSED_RELEASE_MINUTES: _default_for(schema, CONF_INVERTER_DERATING_UNUSED_RELEASE_MINUTES),
        }
    )
    assert safety_result["step_id"] == "safety_limits"
    safety_schema = safety_result["data_schema"]
    assert _default_for(safety_schema, CONF_ARBITRAGE_MODE_MAX_EXPORT_POWER) == pytest.approx(4200)


@pytest.mark.asyncio
async def test_options_flow_settings_defaults_arbitrage_mode_reserve_soc():
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={
            CONF_ARBITRAGE_MODE_RESERVE_SOC: 33,
            CONF_ARBITRAGE_MODE_DEADLINE_HOUR: 7,
        },
    )
    handler = _make_handler(entry)

    result = await handler.async_step_settings()

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "settings"
    assert _default_for(result["data_schema"], CONF_ARBITRAGE_MODE_RESERVE_SOC) == pytest.approx(33)
    assert _default_for(result["data_schema"], CONF_ARBITRAGE_MODE_DEADLINE_HOUR) == 7
    assert _default_for(result["data_schema"], CONF_CAR_USE_BATTERY_ARBITRAGE) == DEFAULT_CAR_USE_BATTERY_ARBITRAGE


@pytest.mark.asyncio
async def test_options_flow_settings_uses_default_arbitrage_mode_settings_when_unset():
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})
    handler = _make_handler(entry)

    result = await handler.async_step_settings()

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "settings"
    assert _default_for(result["data_schema"], CONF_ARBITRAGE_MODE_RESERVE_SOC) == pytest.approx(
        DEFAULT_ARBITRAGE_MODE_RESERVE_SOC
    )
    assert _default_for(result["data_schema"], CONF_CAR_USE_BATTERY_ARBITRAGE) == DEFAULT_CAR_USE_BATTERY_ARBITRAGE
    assert _default_for(result["data_schema"], CONF_ARBITRAGE_MODE_DEADLINE_HOUR) == (
        DEFAULT_ARBITRAGE_MODE_DEADLINE_HOUR
    )


@pytest.mark.asyncio
async def test_options_flow_safety_limits_defaults_arbitrage_mode_export_cap():
    entry = MockConfigEntry(domain=DOMAIN, data={})
    handler = _make_handler(entry)

    await _go_to_entities(
        handler,
        phase_mode=PHASE_MODE_SINGLE,
    )
    result = await handler.async_step_entities(
        {
            CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
            CONF_HIGHEST_PRICE_ENTITY: "sensor.highest_price",
            CONF_LOWEST_PRICE_ENTITY: "sensor.lowest_price",
            CONF_NEXT_PRICE_ENTITY: "sensor.next_price",
            CONF_BATTERY_SOC_ENTITIES: ["sensor.main_battery"],
        }
    )
    assert result["step_id"] == "battery_capacities"
    result = await handler.async_step_battery_capacities(
        {"capacity_sensor_main_battery": 12.5}
    )
    assert result["step_id"] == "settings"

    result = await handler.async_step_settings(
        {
            CONF_MIN_SOC_THRESHOLD: DEFAULT_MIN_SOC,
            CONF_MAX_SOC_THRESHOLD: DEFAULT_MAX_SOC,
            CONF_SOLAR_FORECAST_START_HOUR: 20,
            CONF_MAX_SOC_THRESHOLD_SUNNY: 35,
            CONF_SUNNY_FORECAST_THRESHOLD_KWH: 5.0,
            CONF_PRICE_THRESHOLD: 0.15,
            CONF_PRICE_ADJUSTMENT_MULTIPLIER: DEFAULT_PRICE_ADJUSTMENT_MULTIPLIER,
            CONF_PRICE_ADJUSTMENT_OFFSET: DEFAULT_PRICE_ADJUSTMENT_OFFSET,
            CONF_EMERGENCY_SOC_THRESHOLD: DEFAULT_EMERGENCY_SOC,
            CONF_VERY_LOW_PRICE_THRESHOLD: 30,
            CONF_SIGNIFICANT_SOLAR_THRESHOLD: 1000,
            CONF_USE_DYNAMIC_THRESHOLD: DEFAULT_USE_DYNAMIC_THRESHOLD,
            CONF_DYNAMIC_THRESHOLD_CONFIDENCE: DEFAULT_DYNAMIC_THRESHOLD_CONFIDENCE,
            CONF_USE_AVERAGE_THRESHOLD: DEFAULT_USE_AVERAGE_THRESHOLD,
            CONF_MIN_CAR_CHARGING_DURATION: DEFAULT_MIN_CAR_CHARGING_DURATION,
            CONF_INVERTER_EXPORT_LIMIT: DEFAULT_INVERTER_EXPORT_LIMIT,
            CONF_INVERTER_EXPORT_DEADBAND: DEFAULT_INVERTER_EXPORT_DEADBAND,
            CONF_INVERTER_DERATING_UNUSED_RELEASE_MINUTES: DEFAULT_INVERTER_DERATING_UNUSED_RELEASE_MINUTES,
        }
    )

    assert result["step_id"] == "safety_limits"
    safety_schema = result["data_schema"]
    assert _default_for(safety_schema, CONF_ARBITRAGE_MODE_MAX_EXPORT_POWER) == DEFAULT_ARBITRAGE_MODE_MAX_EXPORT_POWER


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
    handler = _make_handler(entry)

    result = await _go_to_settings(
        handler,
        {
            CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
            CONF_HIGHEST_PRICE_ENTITY: "sensor.highest_price",
            CONF_LOWEST_PRICE_ENTITY: "sensor.lowest_price",
            CONF_NEXT_PRICE_ENTITY: "sensor.next_price",
            CONF_BATTERY_SOC_ENTITIES: [battery_entity],
        },
    )
    assert _default_for(result["data_schema"], CONF_SUNNY_FORECAST_THRESHOLD_KWH) == pytest.approx(7.0)


@pytest.mark.asyncio
async def test_options_flow_skips_phase_assignment_when_single_phase():
    battery_entity = "sensor.main_battery"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_BATTERY_SOC_ENTITIES: [battery_entity]},
        options={
            CONF_BATTERY_SOC_ENTITIES: [battery_entity],
            CONF_BATTERY_CAPACITIES: {battery_entity: 8.0},
            CONF_PHASE_MODE: PHASE_MODE_SINGLE,
        },
    )
    handler = _make_handler(entry)

    await _go_to_entities(handler, phase_mode=PHASE_MODE_SINGLE)
    result = await handler.async_step_entities(
        {
            CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
            CONF_HIGHEST_PRICE_ENTITY: "sensor.highest_price",
            CONF_LOWEST_PRICE_ENTITY: "sensor.lowest_price",
            CONF_NEXT_PRICE_ENTITY: "sensor.next_price",
            CONF_BATTERY_SOC_ENTITIES: [battery_entity],
        }
    )
    assert result["step_id"] == "battery_capacities"
    result = await handler.async_step_battery_capacities(
        {"capacity_sensor_main_battery": 8.0}
    )
    assert result["step_id"] == "settings"


@pytest.mark.asyncio
async def test_options_flow_rejects_invalid_threshold_combination():
    entry = MockConfigEntry(domain=DOMAIN, data={})
    handler = _make_handler(entry)

    await _go_to_entities(handler)
    result = await handler.async_step_entities(
        {
            CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
            CONF_HIGHEST_PRICE_ENTITY: "sensor.highest_price",
            CONF_LOWEST_PRICE_ENTITY: "sensor.lowest_price",
            CONF_NEXT_PRICE_ENTITY: "sensor.next_price",
            CONF_BATTERY_SOC_ENTITIES: [],
        }
    )
    assert result["step_id"] == "settings"
    result = await handler.async_step_settings(
        {
            CONF_MIN_SOC_THRESHOLD: 60,
            CONF_MAX_SOC_THRESHOLD: 40,
        }
    )
    assert result["step_id"] == "safety_limits"
    result = await handler.async_step_safety_limits(
        {
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
    handler = _make_handler(entry)

    await _go_to_entities(handler)
    result = await handler.async_step_entities(
        {
            CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
            CONF_HIGHEST_PRICE_ENTITY: "sensor.highest_price",
            CONF_LOWEST_PRICE_ENTITY: "sensor.lowest_price",
            CONF_NEXT_PRICE_ENTITY: "sensor.next_price",
            CONF_BATTERY_SOC_ENTITIES: [],
        }
    )
    assert result["step_id"] == "settings"
    result = await handler.async_step_settings(
        {
            CONF_MAX_SOC_THRESHOLD: 40,
            CONF_MAX_SOC_THRESHOLD_SUNNY: 70,
        }
    )
    assert result["step_id"] == "safety_limits"
    result = await handler.async_step_safety_limits(
        {
            CONF_MAX_BATTERY_POWER: DEFAULT_MAX_BATTERY_POWER,
            CONF_MAX_CAR_POWER: DEFAULT_MAX_CAR_POWER,
            CONF_MAX_GRID_POWER: DEFAULT_MAX_GRID_POWER,
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
        options={CONF_PHASE_MODE: PHASE_MODE_SINGLE},
    )
    handler = _make_handler(entry)

    await _go_to_entities(handler, phase_mode=PHASE_MODE_THREE)
    result = await handler.async_step_entities(
        {
            CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
            CONF_HIGHEST_PRICE_ENTITY: "sensor.highest_price",
            CONF_LOWEST_PRICE_ENTITY: "sensor.lowest_price",
            CONF_NEXT_PRICE_ENTITY: "sensor.next_price",
            CONF_BATTERY_SOC_ENTITIES: [],
        }
    )
    result = await handler.async_step_settings({})
    result = await handler.async_step_safety_limits(
        {
            CONF_MAX_BATTERY_POWER: DEFAULT_MAX_BATTERY_POWER,
            CONF_MAX_CAR_POWER: DEFAULT_MAX_CAR_POWER,
            CONF_MAX_GRID_POWER: DEFAULT_MAX_GRID_POWER,
        }
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PHASE_MODE] == PHASE_MODE_THREE
    assert result["data"][CONF_SOLAR_PRODUCTION_ENTITY] == "sensor.solar"
    assert result["data"][CONF_HOUSE_CONSUMPTION_ENTITY] == "sensor.house"


@pytest.mark.asyncio
async def test_options_flow_preserves_three_phase_dynamic_fields_when_payload_is_partial():
    battery_entity = "sensor.main_battery"
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_CURRENT_PRICE_ENTITY: "sensor.current_price"},
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
    handler = _make_handler(entry)

    await _go_to_entities(handler, phase_mode=PHASE_MODE_THREE)
    result = await handler.async_step_entities(
        {
            CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
            CONF_HIGHEST_PRICE_ENTITY: "sensor.highest_price",
            CONF_LOWEST_PRICE_ENTITY: "sensor.lowest_price",
            CONF_NEXT_PRICE_ENTITY: "sensor.next_price",
            CONF_BATTERY_SOC_ENTITIES: [battery_entity],
        }
    )
    assert result["step_id"] == "battery_capacities"
    result = await handler.async_step_battery_capacities({})
    assert result["step_id"] == "battery_phase_assignment"
    result = await handler.async_step_battery_phase_assignment({})
    assert result["step_id"] == "settings"
    result = await handler.async_step_settings({})
    result = await handler.async_step_safety_limits(
        {
            CONF_MAX_BATTERY_POWER: DEFAULT_MAX_BATTERY_POWER,
            CONF_MAX_CAR_POWER: DEFAULT_MAX_CAR_POWER,
            CONF_MAX_GRID_POWER: DEFAULT_MAX_GRID_POWER,
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
async def test_options_flow_round_trip_preserves_all_existing_values():
    """Walking through every step with defaults must not lose pre-existing config."""
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
            CONF_MAX_SOC_THRESHOLD: 82,
            CONF_MAX_SOC_THRESHOLD_SUNNY: 38,
            CONF_PRICE_THRESHOLD: 0.19,
            CONF_SUNNY_FORECAST_THRESHOLD_KWH: 6.5,
            CONF_INVERTER_EXPORT_LIMIT: 100,
            CONF_INVERTER_EXPORT_DEADBAND: 50,
            CONF_INVERTER_DERATING_UNUSED_RELEASE_MINUTES: 3,
            CONF_BATTERY_SOC_ENTITIES: [battery_entity],
            CONF_BATTERY_CAPACITIES: {battery_entity: 11.5},
        },
    )
    handler = _make_handler(entry)

    # Walk through every step submitting only schema defaults
    settings_result = await _go_to_settings(
        handler,
        {
            CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
            CONF_HIGHEST_PRICE_ENTITY: "sensor.highest_price",
            CONF_LOWEST_PRICE_ENTITY: "sensor.lowest_price",
            CONF_NEXT_PRICE_ENTITY: "sensor.next_price",
            CONF_BATTERY_SOC_ENTITIES: [battery_entity],
            CONF_SOLAR_PRODUCTION_ENTITY: "sensor.solar_production",
            CONF_HOUSE_CONSUMPTION_ENTITY: "sensor.house_consumption",
        },
    )

    # Submit settings step with defaults (empty dict uses schema defaults)
    settings_schema = settings_result["data_schema"]
    settings_defaults = {
        getattr(key, "schema", ""): _default_for(settings_schema, getattr(key, "schema", ""))
        for key in settings_schema.schema
        if _default_for(settings_schema, getattr(key, "schema", "")) is not None
    }
    result = await handler.async_step_settings(settings_defaults)
    assert result["step_id"] == "safety_limits"

    # Submit safety_limits step with defaults
    safety_schema = result["data_schema"]
    safety_defaults = {
        getattr(key, "schema", ""): _default_for(safety_schema, getattr(key, "schema", ""))
        for key in safety_schema.schema
        if _default_for(safety_schema, getattr(key, "schema", "")) is not None
    }
    result = await handler.async_step_safety_limits(safety_defaults)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    data = result["data"]

    # All pre-existing option values must survive the round trip
    assert data[CONF_MAX_SOC_THRESHOLD] == 82
    assert data[CONF_MAX_SOC_THRESHOLD_SUNNY] == 38
    assert data[CONF_PRICE_THRESHOLD] == pytest.approx(0.19)
    assert data[CONF_SUNNY_FORECAST_THRESHOLD_KWH] == pytest.approx(6.5)
    assert data[CONF_INVERTER_EXPORT_LIMIT] == 100
    assert data[CONF_INVERTER_EXPORT_DEADBAND] == 50
    assert data[CONF_INVERTER_DERATING_UNUSED_RELEASE_MINUTES] == 3
    assert data[CONF_BATTERY_CAPACITIES] == {battery_entity: 11.5}

    # Entity selections from data must also survive
    assert data[CONF_CURRENT_PRICE_ENTITY] == "sensor.current_price"
    assert data[CONF_SOLAR_PRODUCTION_ENTITY] == "sensor.solar_production"
    assert data[CONF_HOUSE_CONSUMPTION_ENTITY] == "sensor.house_consumption"


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
    handler = _make_handler(entry)

    await _go_to_entities(handler, phase_mode=PHASE_MODE_THREE)
    result = await handler.async_step_entities(
        {
            CONF_CURRENT_PRICE_ENTITY: "sensor.current_price",
            CONF_HIGHEST_PRICE_ENTITY: "sensor.highest_price",
            CONF_LOWEST_PRICE_ENTITY: "sensor.lowest_price",
            CONF_NEXT_PRICE_ENTITY: "sensor.next_price",
            CONF_BATTERY_SOC_ENTITIES: [],
            "phase_1_solar_entity": "",
        }
    )
    result = await handler.async_step_settings({})
    result = await handler.async_step_safety_limits(
        {
            CONF_MAX_BATTERY_POWER: DEFAULT_MAX_BATTERY_POWER,
            CONF_MAX_CAR_POWER: DEFAULT_MAX_CAR_POWER,
            CONF_MAX_GRID_POWER: DEFAULT_MAX_GRID_POWER,
        }
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PHASE_MODE] == PHASE_MODE_THREE
    assert result["data"][CONF_PHASES] == {}


@pytest.mark.parametrize("deadline_value", [9.5, "9.5", "abc", -1, 24, True])
def test_validate_config_rejects_non_integral_or_out_of_range_deadline(deadline_value):
    """Deadline hour must remain an integer wall-clock hour."""
    errors = validate_config_consistency(
        {
            CONF_ARBITRAGE_MODE_DEADLINE_HOUR: deadline_value,
        }
    )

    assert any("arbitrage_mode_deadline_hour" in error for error in errors)
