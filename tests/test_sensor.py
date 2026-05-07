"""Sensor entity tests for Electricity Planner."""
from __future__ import annotations

from types import SimpleNamespace

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.electricity_planner.const import DOMAIN
from custom_components.electricity_planner.binary_sensor import FeedinSolarBinarySensor
from custom_components.electricity_planner.sensor import (
    DecisionDiagnosticsSensor,
    EmergencySOCThresholdSensor,
    FeedinPriceThresholdSensor,
    InverterDeratingTargetSensor,
    PhaseGridSetpointSensor,
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


def test_feedin_binary_sensor_exposes_effective_feed_price():
    """Feed-in more-info should show the effective price used by the decision."""
    coordinator = SimpleNamespace(
        data={
            "feedin_effective_price": 0.07,
            "feedin_solar": True,
            "feedin_solar_reason": "Allowed",
            "power_allocation": {"remaining_solar": 500, "total_allocated": 1500},
        },
        config={"feedin_price_threshold": 0.05},
        async_add_listener=lambda update_callback, context=None: lambda: None,
        last_update_success=True,
    )
    entry = MockConfigEntry(domain=DOMAIN, title="Planner", data={})

    sensor = FeedinSolarBinarySensor(coordinator, entry)
    attrs = sensor.extra_state_attributes

    assert attrs["current_price"] == 0.07
    assert attrs["feedin_threshold"] == 0.05


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


def test_phase_grid_setpoint_sensor_exposes_phase_decision():
    """Three-phase installs should expose per-phase setpoint sensors for automations."""
    coordinator = SimpleNamespace(
        data={
            "grid_setpoint": 3000,
            "phase_results": {
                "phase_1": {
                    "grid_setpoint": 1200,
                    "grid_components": {"battery": 700, "car": 500},
                    "charger_limit": 1400,
                    "battery_grid_charging": True,
                    "car_grid_charging": True,
                    "battery_grid_charging_reason": "Battery allowed",
                    "car_grid_charging_reason": "Car allowed",
                    "battery_entities": ["sensor.battery_l1"],
                    "capacity_share": 0.4,
                    "capacity_share_kwh": 8.0,
                }
            },
            "phase_details": {
                "phase_1": {"name": "L1", "grid_power": 650},
            },
        },
        config={"phase_mode": "three_phase"},
        async_add_listener=lambda update_callback, context=None: lambda: None,
        last_update_success=True,
    )
    entry = MockConfigEntry(domain=DOMAIN, title="Planner", data={})

    sensor = PhaseGridSetpointSensor(coordinator, entry, "phase_1")
    attrs = sensor.extra_state_attributes

    assert sensor.native_value == 1200
    assert attrs["aggregate_grid_setpoint"] == 3000
    assert attrs["actual_grid_power"] == 650
    assert attrs["battery_component"] == 700
    assert attrs["car_component"] == 500


def test_decision_diagnostics_exposes_phase_grid_setpoints_as_decisions():
    """Phase setpoints should also be available on the decision diagnostics payload."""
    coordinator = SimpleNamespace(
        data={
            "phase_results": {
                "phase_1": {"grid_setpoint": 1200},
                "phase_2": {"grid_setpoint": 900},
            },
            "phase_mode": "three_phase",
        },
        config={},
        async_add_listener=lambda update_callback, context=None: lambda: None,
        last_update_success=True,
    )
    entry = MockConfigEntry(domain=DOMAIN, title="Planner", data={})

    sensor = DecisionDiagnosticsSensor(coordinator, entry)
    attrs = sensor.extra_state_attributes

    assert attrs["decisions"]["phase_grid_setpoints"] == {
        "phase_1": 1200,
        "phase_2": 900,
    }
    assert attrs["power_outputs"]["phase_grid_setpoints"] == {
        "phase_1": 1200,
        "phase_2": 900,
    }


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
