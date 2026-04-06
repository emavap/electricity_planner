"""Sensor entity tests for Electricity Planner."""
from __future__ import annotations

from types import SimpleNamespace

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.electricity_planner.const import DOMAIN
from custom_components.electricity_planner.sensor import (
    EmergencySOCThresholdSensor,
    FeedinPriceThresholdSensor,
    InverterDeratingTargetSensor,
    PowerAnalysisSensor,
    PriceThresholdSensor,
    VeryLowPriceThresholdSensor,
)


def test_power_analysis_sensor_reports_grid_import_export_with_correct_sign():
    """Positive grid power should be import, negative should be export."""
    coordinator = SimpleNamespace(
        data={
            "power_analysis": {
                "solar_surplus": 250,
            },
            "grid_power": 1200,
        },
        async_add_listener=lambda update_callback, context=None: lambda: None,
        last_update_success=True,
    )
    entry = MockConfigEntry(domain=DOMAIN, title="Planner", data={})

    sensor = PowerAnalysisSensor(coordinator, entry)
    attrs = sensor.extra_state_attributes

    assert attrs["grid_import"] == 1200
    assert attrs["grid_export"] == 0

    coordinator.data["grid_power"] = -800
    attrs = sensor.extra_state_attributes

    assert attrs["grid_import"] == 0
    assert attrs["grid_export"] == 800


def test_price_threshold_sensor_handles_negative_prices():
    """Negative prices must still produce threshold diagnostics."""
    coordinator = SimpleNamespace(
        data={"price_analysis": {"current_price": -0.01}},
        config={"price_threshold": 0.05},
        async_add_listener=lambda update_callback, context=None: lambda: None,
        last_update_success=True,
    )
    entry = MockConfigEntry(domain=DOMAIN, title="Planner", data={})

    sensor = PriceThresholdSensor(coordinator, entry)
    attrs = sensor.extra_state_attributes

    assert attrs["price_below_threshold"] is True
    assert attrs["margin"] == -0.06


def test_feedin_threshold_sensor_handles_zero_price():
    """Zero prices must not be treated as missing in feed-in diagnostics."""
    coordinator = SimpleNamespace(
        data={
            "feedin_effective_price": 0.0,
            "feedin_solar": False,
        },
        config={"feedin_price_threshold": 0.05},
        async_add_listener=lambda update_callback, context=None: lambda: None,
        last_update_success=True,
    )
    entry = MockConfigEntry(domain=DOMAIN, title="Planner", data={})

    sensor = FeedinPriceThresholdSensor(coordinator, entry)
    attrs = sensor.extra_state_attributes

    assert attrs["price_above_threshold"] is False
    assert attrs["margin"] == -0.05


def test_inverter_derating_target_sensor_exposes_control_context():
    """The inverter derating sensor should expose the attributes needed by automations."""
    coordinator = SimpleNamespace(
        data={
            "inverter_derating_target": 1800,
            "inverter_derating_reason": "Hold steady",
            "inverter_derating_alarm": False,
            "inverter_derating_alarm_reason": "No alarm",
            "feedin_solar": False,
            "feedin_solar_reason": "Blocked",
            "grid_power": -80,
            "solar_production": 1800,
            "house_consumption": 1700,
        },
        config={"inverter_export_limit": 100},
        async_add_listener=lambda update_callback, context=None: lambda: None,
        last_update_success=True,
    )
    entry = MockConfigEntry(domain=DOMAIN, title="Planner", data={})

    sensor = InverterDeratingTargetSensor(coordinator, entry)
    attrs = sensor.extra_state_attributes

    assert sensor.native_value == 1800
    assert attrs["feedin_allowed"] is False
    assert attrs["feedin_reason"] == "Blocked"
    assert attrs["grid_power"] == -80
    assert attrs["export_limit_w"] == 100


def test_threshold_sensors_use_native_units():
    """Diagnostic threshold sensors should publish native units for HA UI rendering."""
    coordinator = SimpleNamespace(
        data={},
        config={},
        async_add_listener=lambda update_callback, context=None: lambda: None,
        last_update_success=True,
    )
    entry = MockConfigEntry(domain=DOMAIN, title="Planner", data={})

    assert PriceThresholdSensor(coordinator, entry).native_unit_of_measurement == "€/kWh"
    assert FeedinPriceThresholdSensor(coordinator, entry).native_unit_of_measurement == "€/kWh"
    assert VeryLowPriceThresholdSensor(coordinator, entry).native_unit_of_measurement == "%"
    assert EmergencySOCThresholdSensor(coordinator, entry).native_unit_of_measurement == "%"
