"""Binary sensor entity tests for Electricity Planner."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from homeassistant.util import dt as dt_util

from custom_components.electricity_planner.binary_sensor import (
    BatteryGridChargingBinarySensor,
    CarGridChargingBinarySensor,
    DataAvailabilityBinarySensor,
    FeedinSolarBinarySensor,
    InverterDeratingAlarmBinarySensor,
    LowPriceBinarySensor,
    SolarProductionBinarySensor,
    async_setup_entry,
)
from custom_components.electricity_planner.const import DOMAIN


def _coordinator(data=None, **kwargs):
    defaults = {
        "data": data,
        "config": {},
        "last_successful_update": None,
        "data_unavailable_since": None,
        "notification_sent": False,
        "async_add_listener": lambda update_callback, context=None: lambda: None,
        "last_update_success": True,
        "is_data_available": lambda: True,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _entry():
    return MockConfigEntry(domain=DOMAIN, title="Planner", data={}, entry_id="abc123")


@pytest.mark.asyncio
async def test_async_setup_entry_adds_automation_and_diagnostic_entities():
    """Setup should register all binary sensors in their dashboard groups."""
    entry = _entry()
    coordinator = _coordinator({})
    hass = SimpleNamespace(data={DOMAIN: {entry.entry_id: coordinator}})
    added = []

    def add_entities(entities, update_before_add=False):
        added.extend(entities)
        assert update_before_add is False

    await async_setup_entry(hass, entry, add_entities)

    assert [entity.unique_id for entity in added] == [
        "abc123_battery_grid_charging",
        "abc123_car_grid_charging",
        "abc123_feedin_solar",
        "abc123_inverter_derating_alarm",
        "abc123_low_price",
        "abc123_solar_production",
        "abc123_data_availability",
    ]
    assert added[0].device_info["name"] == "Electricity Planner - Automation Controls"
    assert added[-1].device_info["name"] == "Electricity Planner - Diagnostics & Monitoring"


@pytest.mark.parametrize(
    ("sensor_cls", "data_key", "reason_key", "expected_name"),
    [
        (
            BatteryGridChargingBinarySensor,
            "battery_grid_charging",
            "battery_grid_charging_reason",
            "Battery: Charge from Grid",
        ),
        (
            CarGridChargingBinarySensor,
            "car_grid_charging",
            "car_grid_charging_reason",
            "Car: Charge from Grid",
        ),
    ],
)
def test_grid_charging_binary_sensors_include_phase_breakdown(
    sensor_cls, data_key, reason_key, expected_name
):
    """Grid charging binary sensors expose decision reasons and phase decisions."""
    data = {
        data_key: True,
        reason_key: "cheap slot",
        "phase_results": {"phase_1": {data_key: True}},
        "phase_mode": "three_phase",
    }
    sensor = sensor_cls(_coordinator(data), _entry())

    assert sensor.name == expected_name
    assert sensor.is_on is True
    assert sensor.extra_state_attributes == {
        "reason": "cheap slot",
        "phase_results": {"phase_1": {data_key: True}},
        "phase_mode": "three_phase",
    }

    sensor.coordinator.data = None
    assert sensor.is_on is False
    assert sensor.extra_state_attributes == {}


def test_low_price_binary_sensor_reports_price_details_and_safe_defaults():
    """Low price sensor should be off without price data and detailed with it."""
    sensor = LowPriceBinarySensor(_coordinator({}), _entry())
    assert sensor.is_on is False
    assert sensor.extra_state_attributes == {}

    sensor.coordinator.data = {
        "price_analysis": {
            "is_low_price": True,
            "current_price": 0.03,
            "price_threshold": 0.05,
            "price_ratio": 0.6,
        }
    }

    assert sensor.is_on is True
    assert sensor.extra_state_attributes == {
        "current_price": 0.03,
        "price_threshold": 0.05,
        "price_ratio": 0.6,
    }


def test_solar_production_binary_sensor_reports_power_details():
    """Solar production sensor exposes production diagnostics."""
    sensor = SolarProductionBinarySensor(
        _coordinator(
            {
                "solar_analysis": {
                    "is_producing": True,
                    "current_production": 2200,
                    "house_consumption": 900,
                    "available_surplus": 1300,
                    "production_efficiency": 0.82,
                }
            }
        ),
        _entry(),
    )

    assert sensor.is_on is True
    assert sensor.extra_state_attributes["available_surplus"] == 1300
    assert sensor.extra_state_attributes["production_efficiency"] == 0.82


def test_data_availability_binary_sensor_reports_timestamps_and_sources():
    """Data availability attributes include timestamps, source availability and notifications."""
    now = dt_util.utcnow()
    coordinator = _coordinator(
        {
            "current_price": 0.10,
            "highest_price": 0.30,
            "lowest_price": 0.01,
            "next_price": None,
            "price_analysis": {"data_available": False},
        },
        last_successful_update=now - timedelta(minutes=5),
        data_unavailable_since=now - timedelta(seconds=90),
        notification_sent=True,
        is_data_available=lambda: False,
    )
    sensor = DataAvailabilityBinarySensor(coordinator, _entry())

    assert sensor.is_on is False
    attrs = sensor.extra_state_attributes
    assert attrs["last_successful_update"].startswith(str((now - timedelta(minutes=5)).date()))
    assert attrs["data_unavailable_since"].startswith(str((now - timedelta(seconds=90)).date()))
    assert attrs["unavailable_duration_seconds"] >= 90
    assert attrs["current_price_available"] is True
    assert attrs["next_price_available"] is False
    assert attrs["price_analysis_available"] is False
    assert attrs["notification_sent"] is True


def test_data_availability_binary_sensor_empty_defaults():
    """With no coordinator data, availability sensor keeps a stable attribute shape."""
    sensor = DataAvailabilityBinarySensor(_coordinator(None), _entry())

    assert sensor.is_on is False
    assert sensor.extra_state_attributes == {
        "last_successful_update": None,
        "data_unavailable_since": None,
        "unavailable_duration_seconds": None,
    }


def test_feedin_solar_binary_sensor_uses_default_threshold_and_allocation():
    """Feed-in sensor should fall back to the default threshold when config is absent."""
    sensor = FeedinSolarBinarySensor(
        _coordinator(
            {
                "feedin_solar": True,
                "feedin_solar_reason": "price high enough",
                "feedin_effective_price": 0.12,
                "power_allocation": {"remaining_solar": 450, "total_allocated": 1550},
            },
            config={},
        ),
        _entry(),
    )

    assert sensor.is_on is True
    assert sensor.extra_state_attributes == {
        "reason": "price high enough",
        "current_price": 0.12,
        "feedin_threshold": 0.05,
        "remaining_solar": 450,
        "total_solar_allocated": 1550,
    }


def test_inverter_derating_alarm_binary_sensor_reports_alarm_context():
    """Derating alarm sensor exposes the values needed to debug the alarm."""
    sensor = InverterDeratingAlarmBinarySensor(
        _coordinator(
            {
                "inverter_derating_alarm": True,
                "inverter_derating_alarm_reason": "SOC too low but export limit exceeded",
                "inverter_derating_target": 1800,
                "grid_power": -450,
                "battery_analysis": {"average_soc": 12},
            }
        ),
        _entry(),
    )

    assert sensor.is_on is True
    assert sensor.extra_state_attributes == {
        "reason": "SOC too low but export limit exceeded",
        "inverter_derating_target": 1800,
        "grid_power": -450,
        "battery_soc_average": 12,
    }

    sensor.coordinator.data = None
    assert sensor.is_on is False
    assert sensor.extra_state_attributes == {}
