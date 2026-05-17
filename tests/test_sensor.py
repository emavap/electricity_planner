"""Sensor entity tests for Electricity Planner."""

from __future__ import annotations

from types import SimpleNamespace

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.electricity_planner.binary_sensor import FeedinSolarBinarySensor
from custom_components.electricity_planner.const import DOMAIN
from custom_components.electricity_planner.sensor import (
    BatteryAnalysisSensor,
    BuyPriceMarginSensor,
    ChargerLimitSensor,
    ChargingDecisionSensor,
    DataAvailabilitySensor,
    DecisionDiagnosticsSensor,
    EmergencySOCThresholdSensor,
    EntityStatusSensor,
    FeedinPriceMarginSensor,
    FeedinPriceSensor,
    FeedinPriceThresholdSensor,
    ForecastInsightsSensor,
    GridSetpointSensor,
    InverterDeratingTargetSensor,
    NordPoolPricesSensor,
    PhaseGridSetpointSensor,
    PowerAnalysisSensor,
    PriceAnalysisSensor,
    PriceThresholdSensor,
    SignificantSolarThresholdSensor,
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

    assert (
        PriceThresholdSensor(coordinator, entry).native_unit_of_measurement == "€/kWh"
    )
    assert (
        FeedinPriceThresholdSensor(coordinator, entry).native_unit_of_measurement
        == "€/kWh"
    )
    assert (
        VeryLowPriceThresholdSensor(coordinator, entry).native_unit_of_measurement
        == "%"
    )
    assert (
        EmergencySOCThresholdSensor(coordinator, entry).native_unit_of_measurement
        == "%"
    )


def _entry():
    return MockConfigEntry(domain=DOMAIN, title="Planner", data={}, entry_id="entry1")


def _coordinator(data=None, config=None, **kwargs):
    defaults = {
        "data": data,
        "config": config or {},
        "async_add_listener": lambda update_callback, context=None: lambda: None,
        "last_update_success": True,
        "last_successful_update": None,
        "data_unavailable_since": None,
        "notification_sent": False,
        "is_data_available": lambda: True,
        "_resolve_transport_cost": lambda lookup, start, reference_now=None: None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_core_analysis_sensors_report_values_and_empty_defaults():
    """Analysis sensors should expose native values and detailed attributes."""
    entry = _entry()
    coordinator = _coordinator(
        {
            "battery_analysis": {
                "average_soc": 55,
                "min_soc": 20,
                "max_soc": 90,
                "batteries_count": 2,
                "batteries_full": False,
                "min_soc_threshold": 15,
                "max_soc_threshold": 95,
                "remaining_capacity_percent": 40,
            },
            "price_analysis": {
                "current_price": 0.12,
                "highest_price": 0.30,
                "lowest_price": 0.02,
                "next_price": 0.14,
                "raw_current_price": 0.10,
                "raw_highest_price": 0.28,
                "raw_lowest_price": 0.01,
                "raw_next_price": 0.13,
                "price_adjustment_multiplier": 1.1,
                "price_adjustment_offset": 0.01,
                "transport_cost": 0.02,
                "price_threshold": 0.15,
                "dynamic_threshold": 0.11,
                "is_low_price": True,
                "is_lowest_price": False,
                "price_position": 0.25,
                "next_price_higher": True,
                "price_trend_improving": False,
                "very_low_price": True,
            },
            "grid_power": -250,
            "power_analysis": {
                "solar_surplus": 1500,
                "solar_production": 2600,
                "house_consumption": 900,
                "car_charging_power": 400,
                "battery_charging_power": 300,
                "has_solar_surplus": True,
                "significant_solar_surplus": True,
            },
        }
    )

    battery = BatteryAnalysisSensor(coordinator, entry)
    assert battery.native_value == 55
    assert battery.extra_state_attributes["remaining_capacity_percent"] == 40

    price = PriceAnalysisSensor(coordinator, entry)
    assert price.native_value == 0.12
    assert price.extra_state_attributes["raw_highest_price"] == 0.28
    assert price.extra_state_attributes["very_low_price"] is True

    power = PowerAnalysisSensor(coordinator, entry)
    assert power.native_value == 1500
    attrs = power.extra_state_attributes
    assert attrs["solar_production"] == 2600
    assert attrs["grid_import"] == 0
    assert attrs["grid_export"] == 250

    coordinator.data = {}
    assert battery.native_value is None
    assert battery.extra_state_attributes == {}
    assert price.native_value is None
    assert price.extra_state_attributes == {}


def test_charging_decision_sensor_covers_all_decision_states():
    """Decision sensor states should summarize battery/car combinations."""
    entry = _entry()
    coordinator = _coordinator({"price_analysis": {"data_available": True}})
    sensor = ChargingDecisionSensor(coordinator, entry)

    coordinator.data.update(
        {
            "battery_grid_charging": True,
            "car_grid_charging": True,
            "battery_grid_charging_reason": "battery cheap",
            "car_grid_charging_reason": "car cheap",
            "phase_mode": "three_phase",
            "phase_results": {"phase_1": {"battery_grid_charging": True}},
        }
    )
    assert sensor.native_value == "charge_both_from_grid"
    assert sensor.extra_state_attributes["phase_results"] == {
        "phase_1": {"battery_grid_charging": True}
    }

    coordinator.data["car_grid_charging"] = False
    assert sensor.native_value == "charge_battery: battery cheap"
    coordinator.data["battery_grid_charging"] = False
    coordinator.data["car_grid_charging"] = True
    assert sensor.native_value == "charge_car: car cheap"
    coordinator.data["car_grid_charging"] = False
    assert sensor.native_value == "no_charging: battery cheap"
    coordinator.data = None
    assert sensor.native_value == "no_data_available"
    assert sensor.extra_state_attributes == {"data_available": False}


def test_entity_status_sensor_summarizes_and_flattens_categories():
    """Entity status sensor should summarize all/partial/missing availability."""
    entry = _entry()
    coordinator = _coordinator(
        {
            "entity_status": {
                "summary": {"available": 2, "unavailable": 1, "total_configured": 3},
                "price_entities": {
                    "current": {
                        "configured": True,
                        "entity_id": "sensor.price",
                        "status": "available",
                    }
                },
                "battery_entities": {
                    "soc": {
                        "configured": True,
                        "entity_id": "sensor.soc",
                        "status": "unavailable",
                        "reason": "missing",
                        "is_required": True,
                    }
                },
                "power_entities": {},
                "optional_entities": {
                    "solar": {
                        "configured": False,
                        "entity_id": "sensor.solar",
                        "status": "unknown",
                    }
                },
            }
        }
    )
    sensor = EntityStatusSensor(coordinator, entry)

    assert sensor.native_value == "1_unavailable"
    assert sensor.icon == "mdi:alert"
    attrs = sensor.extra_state_attributes
    assert attrs["available_entities"] == ["sensor.price"]
    assert attrs["unavailable_entities"] == [
        {
            "entity_id": "sensor.soc",
            "status": "unavailable",
            "reason": "missing",
            "is_required": True,
        }
    ]

    coordinator.data["entity_status"]["summary"] = {
        "available": 0,
        "unavailable": 0,
        "total_configured": 0,
    }
    assert sensor.native_value == "no_entities"
    coordinator.data["entity_status"]["summary"] = {
        "available": 3,
        "unavailable": 0,
        "total_configured": 3,
    }
    assert sensor.native_value == "all_available"
    coordinator.data["entity_status"]["summary"] = {
        "available": 0,
        "unavailable": 3,
        "total_configured": 3,
    }
    assert sensor.native_value == "all_unavailable"


def test_limit_and_setpoint_sensors_include_override_context():
    """Automation numeric sensors expose override state and power context."""
    entry = _entry()
    coordinator = _coordinator(
        {
            "charger_limit": 4200,
            "charger_limit_reason": "solar surplus",
            "grid_setpoint": 6500,
            "grid_setpoint_reason": "peak guard",
            "monthly_grid_peak": "5000",
            "power_analysis": {
                "car_charging_power": 1500,
                "solar_surplus": 2200,
                "car_currently_charging": True,
            },
            "battery_analysis": {"average_soc": 61},
            "manual_overrides": {
                "charger_limit": {
                    "value": 1600,
                    "reason": "manual",
                    "expires_at": "soon",
                },
                "grid_setpoint": {
                    "value": -500,
                    "reason": "export",
                    "expires_at": "later",
                },
            },
            "phase_mode": "three_phase",
            "phase_results": {
                "phase_1": {"grid_setpoint": 2000, "grid_components": {"battery": 1200}}
            },
        },
        config={"base_grid_setpoint": 3000},
    )

    charger = ChargerLimitSensor(coordinator, entry)
    assert charger.native_value == 4200
    assert charger.extra_state_attributes["override_value"] == 1600
    assert charger.extra_state_attributes["car_currently_charging"] is True

    grid = GridSetpointSensor(coordinator, entry)
    assert grid.native_value == 6500
    attrs = grid.extra_state_attributes
    assert attrs["override_reason"] == "export"
    assert attrs["base_grid_setpoint"] == 3000
    assert attrs["phase_grid_setpoints"] == {"phase_1": 2000}

    coordinator.data["manual_overrides"] = {}
    assert charger.extra_state_attributes["is_overridden"] is False
    assert grid.extra_state_attributes["is_overridden"] is False


def test_forecast_and_data_availability_sensors():
    """Forecast and availability sensors cover available and empty data branches."""
    entry = _entry()
    coordinator = _coordinator(
        {
            "forecast_summary": {
                "available": True,
                "cheapest_interval_price": 0.01,
                "window_count": 3,
            }
        },
        data_unavailable_since=None,
        notification_sent=False,
        is_data_available=lambda: True,
    )

    forecast = ForecastInsightsSensor(coordinator, entry)
    assert forecast.native_value == 0.01
    assert forecast.extra_state_attributes["window_count"] == 3

    availability = DataAvailabilitySensor(coordinator, entry)
    assert availability.native_value == 0
    assert availability.extra_state_attributes["data_currently_available"] is True

    coordinator.data = {"forecast_summary": {"available": False}}
    assert forecast.native_value is None
    coordinator.data = None
    assert forecast.extra_state_attributes == {}


def test_price_margin_and_threshold_sensors_cover_status_bands():
    """Price/feed-in margin sensors should classify cheap, watch, profitable and loss bands."""
    entry = _entry()
    coordinator = _coordinator(
        {
            "price_analysis": {
                "current_price": 0.10,
                "price_threshold": 0.12,
                "very_low_price": True,
                "price_position": 0.2,
            },
            "feedin_effective_price": 0.07,
            "feedin_solar": True,
            "power_analysis": {
                "solar_surplus": 1400,
                "significant_solar_surplus": True,
            },
            "battery_analysis": {"average_soc": 10},
        },
        config={
            "feedin_price_threshold": 0.05,
            "very_low_price_threshold": 25,
            "significant_solar_threshold": 1000,
            "emergency_soc_threshold": 15,
        },
    )

    buy_margin = BuyPriceMarginSensor(coordinator, entry)
    assert buy_margin.native_value == "favorable"
    assert buy_margin.extra_state_attributes["margin"] == -0.02
    coordinator.data["price_analysis"]["current_price"] = 0.13
    assert buy_margin.native_value == "watch"
    coordinator.data["price_analysis"]["current_price"] = 0.20
    assert buy_margin.native_value == "expensive"

    feed_margin = FeedinPriceMarginSensor(coordinator, entry)
    assert feed_margin.native_value == "profitable"
    coordinator.data["feedin_effective_price"] = 0.04
    assert feed_margin.native_value == "near"
    coordinator.data["feedin_effective_price"] = 0.01
    assert feed_margin.native_value == "loss"
    assert feed_margin.extra_state_attributes["margin"] == 0.04

    very_low = VeryLowPriceThresholdSensor(coordinator, entry)
    assert very_low.native_value == 25
    assert very_low.extra_state_attributes["price_position"] == "20%"

    significant_solar = SignificantSolarThresholdSensor(coordinator, entry)
    assert significant_solar.native_value == 1000
    assert significant_solar.extra_state_attributes["margin"] == 400

    emergency = EmergencySOCThresholdSensor(coordinator, entry)
    assert emergency.native_value == 15
    assert emergency.extra_state_attributes["is_emergency"] is True


def test_feedin_price_sensor_and_nordpool_price_helpers():
    """Feed-in price and Nord Pool helper branches are covered with compact intervals."""
    entry = _entry()
    coordinator = _coordinator(
        {
            "feedin_effective_price": 0.08,
            "feedin_solar": True,
            "feedin_solar_reason": "profitable",
            "price_analysis": {
                "raw_current_price": 0.04,
                "price_adjustment_multiplier": 1.2,
                "price_adjustment_offset": 0.01,
            },
            "power_allocation": {"remaining_solar": 500},
            "transport_cost": 0.02,
            "nordpool_prices_today": {
                "BE": [{"start": "2026-05-17T10:00:00+02:00", "value": 100.0}]
            },
            "nordpool_prices_tomorrow": {
                "BE": [{"start": "2026-05-18T10:00:00+02:00", "price": 80.0}]
            },
            "transport_cost_status": "fallback_current",
        },
        config={
            "feedin_adjustment_multiplier": 0.8,
            "feedin_adjustment_offset": -0.01,
            "feedin_price_threshold": 0.05,
        },
    )

    feed = FeedinPriceSensor(coordinator, entry)
    assert feed.native_value == 0.08
    attrs = feed.extra_state_attributes
    assert attrs["feedin_multiplier"] == 0.8
    assert attrs["remaining_solar"] == 500

    nordpool = NordPoolPricesSensor(coordinator, entry)
    assert nordpool.native_value == "today+tomorrow (2 intervals)"
    attrs = nordpool.extra_state_attributes
    assert attrs["total_intervals"] == 2
    assert attrs["transport_cost_applied"] is True
    assert attrs["data"][0]["price"] == 0.129
    assert nordpool.extra_state_attributes == attrs  # cached copy branch
    assert nordpool._compact_price_interval({"start": None, "price": 1}) is None
    assert nordpool._normalize_price_interval("bad") is None
    assert nordpool._normalize_price_interval({"start": "bad"}) is None
