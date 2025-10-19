"""Config flow tests for Electricity Planner."""
from __future__ import annotations

import pytest
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.electricity_planner.config_flow import OptionsFlowHandler
from custom_components.electricity_planner.const import (
    CONF_BATTERY_CAPACITIES,
    CONF_BATTERY_SOC_ENTITIES,
    CONF_BASE_GRID_SETPOINT,
    CONF_DYNAMIC_THRESHOLD_CONFIDENCE,
    CONF_EMERGENCY_SOC_THRESHOLD,
    CONF_FEEDIN_ADJUSTMENT_MULTIPLIER,
    CONF_FEEDIN_ADJUSTMENT_OFFSET,
    CONF_FEEDIN_PRICE_THRESHOLD,
    CONF_CURRENT_PRICE_ENTITY,
    CONF_HIGHEST_PRICE_ENTITY,
    CONF_LOWEST_PRICE_ENTITY,
    CONF_NEXT_PRICE_ENTITY,
    CONF_SOLAR_PRODUCTION_ENTITY,
    CONF_HOUSE_CONSUMPTION_ENTITY,
    CONF_PHASE_MODE,
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
    CONF_SOLAR_PEAK_EMERGENCY_SOC,
    CONF_USE_AVERAGE_THRESHOLD,
    CONF_USE_DYNAMIC_THRESHOLD,
    CONF_VERY_LOW_PRICE_THRESHOLD,
    CONF_MAX_SOC_THRESHOLD,
    DEFAULT_BASE_GRID_SETPOINT,
    DEFAULT_DYNAMIC_THRESHOLD_CONFIDENCE,
    DEFAULT_EMERGENCY_SOC,
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
    DEFAULT_SOLAR_PEAK_EMERGENCY_SOC,
    DEFAULT_USE_AVERAGE_THRESHOLD,
    DEFAULT_USE_DYNAMIC_THRESHOLD,
    DEFAULT_VERY_LOW_PRICE_THRESHOLD,
    DEFAULT_MAX_SOC,
    PHASE_MODE_SINGLE,
    DOMAIN,
)


@pytest.mark.asyncio
async def test_options_flow_returns_updated_options():
    entry = MockConfigEntry(domain=DOMAIN, data={})
    handler = OptionsFlowHandler(entry)

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
        CONF_SOLAR_PEAK_EMERGENCY_SOC: DEFAULT_SOLAR_PEAK_EMERGENCY_SOC,
        CONF_PREDICTIVE_CHARGING_MIN_SOC: DEFAULT_PREDICTIVE_CHARGING_MIN_SOC,
        CONF_BASE_GRID_SETPOINT: DEFAULT_BASE_GRID_SETPOINT,
        CONF_BATTERY_SOC_ENTITIES: [battery_entity],
        "capacity_sensor_main_battery": 12.5,
    }

    result = await handler.async_step_init(user_input)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PRICE_THRESHOLD] == 0.123
    assert result["data"][CONF_USE_DYNAMIC_THRESHOLD] is (not DEFAULT_USE_DYNAMIC_THRESHOLD)
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

    handler = OptionsFlowHandler(entry)
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

    capacity_field = f"capacity_{battery_entity.replace('.', '_')}"
    assert default_for(capacity_field) == pytest.approx(11.5)


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

    handler = OptionsFlowHandler(entry)
    result = await handler.async_step_init()

    assert result["type"] == FlowResultType.FORM
    schema = result["data_schema"].schema

    phase_assignment_field = f"phase_assignment_{battery_entity.replace('.', '_')}"
    assert not any(getattr(field, "schema", None) == phase_assignment_field for field in schema)
