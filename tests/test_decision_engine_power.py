"""Tests for grid setpoint, charger limits, and feed-in decisions."""
import pytest

from custom_components.electricity_planner.const import (
    CONF_BASE_GRID_SETPOINT,
    CONF_MAX_BATTERY_POWER,
    CONF_MAX_CAR_POWER,
    CONF_MAX_GRID_POWER,
    CONF_MIN_CAR_CHARGING_THRESHOLD,
    CONF_FEEDIN_PRICE_THRESHOLD,
    CONF_FEEDIN_ADJUSTMENT_MULTIPLIER,
    CONF_FEEDIN_ADJUSTMENT_OFFSET,
    CONF_PRICE_ADJUSTMENT_MULTIPLIER,
    CONF_PRICE_ADJUSTMENT_OFFSET,
    CONF_VERY_LOW_PRICE_THRESHOLD,
)
from custom_components.electricity_planner.decision_engine import ChargingDecisionEngine


def _engine(config=None):
    base_config = {
        CONF_MAX_GRID_POWER: 12000,
        CONF_MAX_BATTERY_POWER: 4000,
        CONF_MAX_CAR_POWER: 7000,
        CONF_BASE_GRID_SETPOINT: 3000,
        CONF_MIN_CAR_CHARGING_THRESHOLD: 100,
        CONF_VERY_LOW_PRICE_THRESHOLD: 30,
    }
    if config:
        base_config.update(config)
    return ChargingDecisionEngine(hass=None, config=base_config)


def test_grid_setpoint_without_battery_data():
    engine = _engine()
    price_analysis = {}
    battery_analysis = {"average_soc": None}
    power_allocation = {"solar_for_car": 500, "car_current_solar_usage": 200}
    data = {
        "car_charging_power": 5000,
        "car_grid_charging": True,
        "battery_grid_charging": False,
        "monthly_grid_peak": 4000,
    }

    result = engine._calculate_grid_setpoint(
        price_analysis,
        battery_analysis,
        power_allocation,
        data,
        charger_limit=6000,
    )

    assert result["grid_setpoint"] == int(4000 * 0.9)
    assert "grid only for car" in result["grid_setpoint_reason"]


def test_grid_setpoint_distributes_between_car_and_battery():
    engine = _engine()
    price_analysis = {}
    battery_analysis = {"average_soc": 80, "max_soc_threshold": 90}
    power_allocation = {
        "solar_for_car": 1000,
        "car_current_solar_usage": 500,
    }
    data = {
        "car_charging_power": 6000,
        "car_grid_charging": True,
        "battery_grid_charging": True,
        "monthly_grid_peak": 8000,
    }

    result = engine._calculate_grid_setpoint(
        price_analysis,
        battery_analysis,
        power_allocation,
        data,
        charger_limit=7000,
    )

    assert result["grid_setpoint"] == int(8000 * 0.9)
    reason = result["grid_setpoint_reason"]
    assert "car 4500W" in reason
    assert "battery 2700W" in reason


def test_grid_setpoint_zero_for_solar_only_car():
    engine = _engine()
    battery_analysis = {"average_soc": 70, "max_soc_threshold": 90}
    power_allocation = {"solar_for_car": 2500, "car_current_solar_usage": 500}
    data = {
        "car_charging_power": 3000,
        "car_grid_charging": True,
        "battery_grid_charging": False,
        "car_solar_only": True,
    }

    result = engine._calculate_grid_setpoint(
        {},
        battery_analysis,
        power_allocation,
        data,
        charger_limit=3000,
    )

    assert result["grid_setpoint"] == 0
    assert "Solar-only car charging" in result["grid_setpoint_reason"]


@pytest.mark.parametrize(
    ("current_price", "threshold", "expected"),
    [
        (0.08, 0.05, True),
        (0.03, 0.05, False),
    ],
)
def test_feed_in_decision(current_price, threshold, expected):
    engine = _engine({CONF_VERY_LOW_PRICE_THRESHOLD: 25})
    price_analysis = {"current_price": current_price, "data_available": True, "raw_current_price": current_price}
    power_allocation = {"remaining_solar": 1800}
    engine.config[CONF_FEEDIN_PRICE_THRESHOLD] = threshold

    result = engine._decide_feedin_solar(price_analysis, power_allocation)

    assert result["feedin_solar"] is expected
    if expected:
        assert "enable" in result["feedin_solar_reason"]
    else:
        assert "disable" in result["feedin_solar_reason"]
    assert "feedin_effective_price" in result


def test_feed_in_uses_adjustment_positive():
    engine = _engine({
        CONF_FEEDIN_ADJUSTMENT_MULTIPLIER: 0.7,
        CONF_FEEDIN_ADJUSTMENT_OFFSET: -0.01,
        CONF_FEEDIN_PRICE_THRESHOLD: 0.0,
    })
    price_analysis = {
        "data_available": True,
        "current_price": 0.09,
        "raw_current_price": 0.09,
    }

    result = engine._decide_feedin_solar(price_analysis, {"remaining_solar": 1000})

    assert result["feedin_solar"] is True
    assert result["feedin_effective_price"] == pytest.approx(0.09 * 0.7 - 0.01)
    assert "Net feed-in price" in result["feedin_solar_reason"]


def test_feed_in_uses_adjustment_negative():
    engine = _engine({
        CONF_FEEDIN_ADJUSTMENT_MULTIPLIER: 0.7,
        CONF_FEEDIN_ADJUSTMENT_OFFSET: -0.01,
        CONF_FEEDIN_PRICE_THRESHOLD: 0.0,
    })
    price_analysis = {
        "data_available": True,
        "current_price": 0.01,
        "raw_current_price": 0.01,
    }

    result = engine._decide_feedin_solar(price_analysis, {"remaining_solar": 1000})

    assert result["feedin_solar"] is False
    assert result["feedin_effective_price"] == pytest.approx(0.01 * 0.7 - 0.01)
    assert "disable" in result["feedin_solar_reason"]


def test_feed_in_no_price_disables():
    engine = _engine()
    price_analysis = {"data_available": True, "current_price": None}

    result = engine._decide_feedin_solar(price_analysis, {"remaining_solar": 500})

    assert result["feedin_solar"] is False
    assert result["feedin_effective_price"] is None
    assert "No adjusted price available" in result["feedin_solar_reason"]


def test_feed_in_adjustment_respects_threshold():
    engine = _engine({
        CONF_FEEDIN_ADJUSTMENT_MULTIPLIER: 0.7,
        CONF_FEEDIN_ADJUSTMENT_OFFSET: -0.01,
        CONF_FEEDIN_PRICE_THRESHOLD: 0.02,
    })
    price_analysis = {
        "data_available": True,
        "current_price": 0.05,
        "raw_current_price": 0.05,
    }

    result = engine._decide_feedin_solar(price_analysis, {"remaining_solar": 800})

    assert result["feedin_effective_price"] == pytest.approx(0.05 * 0.7 - 0.01)
    assert result["feedin_solar"] is True
    assert "0.020" in result["feedin_solar_reason"]


def test_price_analysis_unavailable_when_adjustment_missing_data():
    engine = _engine({
        CONF_PRICE_ADJUSTMENT_MULTIPLIER: 1.12,
        CONF_PRICE_ADJUSTMENT_OFFSET: 0.008,
    })
    analysis = engine._analyze_comprehensive_pricing(
        {
            "current_price": None,
            "highest_price": 0.20,
            "lowest_price": 0.05,
            "next_price": 0.10,
        }
    )

    assert analysis["data_available"] is False


def test_transport_cost_added_to_pricing():
    engine = _engine()
    analysis = engine._analyze_comprehensive_pricing(
        {
            "current_price": 0.10,
            "highest_price": 0.20,
            "lowest_price": 0.05,
            "next_price": 0.12,
            "transport_cost": 0.02,
        }
    )

    assert analysis["current_price"] == pytest.approx(0.12)
    assert analysis["transport_cost"] == pytest.approx(0.02)


def test_charger_limit_enforces_restrictions():
    engine = _engine()
    price_analysis = {}
    battery_analysis = {"average_soc": 60, "max_soc_threshold": 90}
    power_allocation = {"solar_for_car": 2000}
    data = {
        "car_charging_power": 3500,
        "car_grid_charging": False,
    }

    result = engine._calculate_charger_limit(
        price_analysis,
        battery_analysis,
        power_allocation,
        data,
    )

    assert result["charger_limit"] == 1400
    assert "not allowed" in result["charger_limit_reason"]


def test_charger_limit_for_solar_only_car():
    engine = _engine()
    price_analysis = {}
    battery_analysis = {"average_soc": 85, "max_soc_threshold": 90}
    power_allocation = {"solar_for_car": 2500}
    data = {
        "car_charging_power": 3000,
        "car_grid_charging": True,
        "car_solar_only": True,
    }

    result = engine._calculate_charger_limit(
        price_analysis,
        battery_analysis,
        power_allocation,
        data,
    )

    assert result["charger_limit"] == 2500
    assert "Solar-only car charging" in result["charger_limit_reason"]


def test_price_adjustment_failure_disables_charging():
    """If price adjustments are configured but fail, charging must be disabled for safety."""
    engine = _engine({
        CONF_PRICE_ADJUSTMENT_MULTIPLIER: 1.12,
        CONF_PRICE_ADJUSTMENT_OFFSET: 0.008,
    })

    # Simulate adjustment failure by passing invalid data that will cause apply_price_adjustment to return None
    # In practice this would be a bug in apply_price_adjustment, but we test the safety behavior
    data = {
        "current_price": None,  # This will cause adjustment to return None
        "highest_price": 0.20,
        "lowest_price": 0.05,
        "next_price": 0.09,
    }

    price_analysis = engine._analyze_comprehensive_pricing(data)

    # When adjustments are configured and fail, data should be treated as unavailable
    assert price_analysis["data_available"] is False
    assert price_analysis["current_price"] is None


def test_price_adjustment_fallback_only_when_no_adjustment():
    """Without configured adjustments, fallback to raw prices is safe."""
    engine = _engine({
        CONF_PRICE_ADJUSTMENT_MULTIPLIER: 1.0,  # Default = no adjustment
        CONF_PRICE_ADJUSTMENT_OFFSET: 0.0,      # Default = no adjustment
    })

    data = {
        "current_price": 0.08,
        "highest_price": 0.20,
        "lowest_price": 0.05,
        "next_price": 0.09,
    }

    price_analysis = engine._analyze_comprehensive_pricing(data)

    # Without adjustments configured, raw prices are used normally
    assert price_analysis["data_available"] is True
    assert price_analysis["current_price"] == 0.08
