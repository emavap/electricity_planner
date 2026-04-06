"""Tests for grid setpoint, charger limits, feed-in, and phase handling."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from custom_components.electricity_planner.const import (
    CONF_BASE_GRID_SETPOINT,
    CONF_INVERTER_DERATING_SOC_BYPASS_THRESHOLD,
    CONF_INVERTER_DERATING_UNUSED_RELEASE_MINUTES,
    CONF_INVERTER_EXPORT_DEADBAND,
    CONF_INVERTER_EXPORT_LIMIT,
    CONF_MAX_BATTERY_POWER,
    CONF_MAX_CAR_POWER,
    CONF_MAX_GRID_POWER,
    CONF_MAX_INVERTER_POWER,
    CONF_MAX_SOC_THRESHOLD,
    CONF_MAX_SOC_THRESHOLD_SUNNY,
    CONF_SUNNY_FORECAST_THRESHOLD_KWH,
    CONF_MIN_CAR_CHARGING_THRESHOLD,
    CONF_FEEDIN_PRICE_THRESHOLD,
    CONF_FEEDIN_ADJUSTMENT_MULTIPLIER,
    CONF_FEEDIN_ADJUSTMENT_OFFSET,
    CONF_PRICE_ADJUSTMENT_MULTIPLIER,
    CONF_PRICE_ADJUSTMENT_OFFSET,
    CONF_VERY_LOW_PRICE_THRESHOLD,
    CONF_PHASE_MODE,
    PHASE_MODE_SINGLE,
    PHASE_MODE_THREE,
    PHASE_IDS,
    DEFAULT_MAX_SOC,
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

    assert result["grid_setpoint"] == 3600
    assert "grid import reserved for car pulling 3600W" in result["grid_setpoint_reason"]
    assert "Peak this month is 4000W" in result["grid_setpoint_reason"]
    assert "using 3600W" in result["grid_setpoint_reason"]
    assert "(90% of 4000W)" in result["grid_setpoint_reason"]


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

    assert result["grid_setpoint"] == 7200
    reason = result["grid_setpoint_reason"]
    assert "car pulling 4500W" in reason
    assert "battery charging 2700W" in reason
    assert "Grid import reserved for" in reason
    assert "Peak this month is 8000W" in reason
    assert "max allowed peak is 3000W" in reason
    assert "using 7200W" in reason
    assert "(90% of 8000W)" in reason


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


def test_grid_setpoint_honors_charger_limit_cap():
    engine = _engine()
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
        {},
        battery_analysis,
        power_allocation,
        data,
        charger_limit=3000,
    )

    assert result["grid_setpoint"] == 5500
    assert result["grid_components"]["car"] == 1500
    assert result["grid_components"]["battery"] == 4000
    assert "car pulling 1500W" in result["grid_setpoint_reason"]
    assert "battery charging 4000W" in result["grid_setpoint_reason"]


def test_battery_grid_charging_full_reason_uses_default_max_soc_when_missing():
    engine = _engine()

    result = engine._decide_battery_grid_charging(
        price_analysis={"data_available": True},
        battery_analysis={
            "batteries_count": 1,
            "batteries_available": True,
            "batteries_full": True,
            "average_soc": 75,
        },
        power_allocation={},
        power_analysis={},
        time_context={},
        data={},
    )

    assert result["battery_grid_charging"] is False
    assert f"≥ {DEFAULT_MAX_SOC}% threshold" in result["battery_grid_charging_reason"]


def test_battery_decision_context_uses_sanitized_settings():
    engine = _engine(
        {
            "emergency_soc_threshold": "bad",
            "soc_price_multiplier_max": "bad",
            "soc_buffer_target": "bad",
        }
    )

    captured_context: dict[str, object] = {}

    def _capture(context):
        captured_context.update(context)
        return False, "captured"

    engine.strategy_manager.evaluate = _capture
    engine.strategy_manager.get_last_trace = lambda: []

    result = engine._decide_battery_grid_charging(
        price_analysis={"data_available": True, "current_price": 0.2, "price_threshold": 0.15},
        battery_analysis={"batteries_count": 1, "batteries_available": True, "batteries_full": False, "average_soc": 20},
        power_allocation={},
        power_analysis={"significant_solar_surplus": False, "solar_surplus": 0},
        time_context={},
        data={},
    )

    assert result["battery_grid_charging"] is False
    assert captured_context["settings"] is engine._settings
    assert engine._settings.emergency_soc_threshold == 15
    assert engine._settings.soc_price_multiplier_max == 1.3
    assert engine._settings.soc_buffer_target == 50


def test_safe_grid_setpoint_uses_current_month_peak_when_already_above_configured_limit():
    engine = _engine({CONF_BASE_GRID_SETPOINT: 5000})

    assert engine._get_safe_grid_setpoint(5100) == 4590


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
    assert "Net feed-in price" in result["feedin_solar_reason"]


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


def test_feed_in_reason_uses_effective_feed_price_with_default_contract_values():
    engine = _engine({
        CONF_PRICE_ADJUSTMENT_MULTIPLIER: 1.04,
        CONF_PRICE_ADJUSTMENT_OFFSET: 0.005,
        CONF_FEEDIN_ADJUSTMENT_MULTIPLIER: 1.0,
        CONF_FEEDIN_ADJUSTMENT_OFFSET: -0.0098,
        CONF_FEEDIN_PRICE_THRESHOLD: 0.01,
    })
    price_analysis = {
        "data_available": True,
        "current_price": 0.0568,
        "raw_current_price": 0.02,
    }

    result = engine._decide_feedin_solar(price_analysis, {"remaining_solar": 600})

    assert result["feedin_effective_price"] == pytest.approx(0.0102)
    assert result["feedin_solar"] is True
    assert "Net feed-in price 0.010€/kWh" in result["feedin_solar_reason"]
    assert "0.056" not in result["feedin_solar_reason"]


def test_inverter_derating_gradually_reopens_when_export_below_band():
    engine = _engine(
        {
            CONF_MAX_INVERTER_POWER: 4400,
            CONF_INVERTER_EXPORT_LIMIT: 80,
            CONF_INVERTER_EXPORT_DEADBAND: 40,
            CONF_INVERTER_DERATING_SOC_BYPASS_THRESHOLD: 95,
        }
    )

    result = engine._calculate_inverter_derating_target(
        {
            "feedin_solar": False,
            "solar_production": 2500,
            "grid_power": -20,
            "previous_inverter_derating_target": 1800,
            "battery_analysis": {"average_soc": 98},
        }
    )

    assert result["inverter_derating_target"] == 1900
    assert "reopen inverter gradually" in result["inverter_derating_reason"]
    assert result["inverter_derating_alarm"] is False


def test_inverter_derating_holds_previous_target_inside_deadband():
    engine = _engine(
        {
            CONF_MAX_INVERTER_POWER: 4400,
            CONF_INVERTER_EXPORT_LIMIT: 80,
            CONF_INVERTER_EXPORT_DEADBAND: 40,
        }
    )

    result = engine._calculate_inverter_derating_target(
        {
            "feedin_solar": False,
            "solar_production": 2100,
            "grid_power": -80,
            "previous_inverter_derating_target": 1800,
            "battery_analysis": {"average_soc": 98},
        }
    )

    assert result["inverter_derating_target"] == 1800
    assert "hold the current derating target steady" in result["inverter_derating_reason"]
    assert result["inverter_derating_alarm"] is False


def test_inverter_derating_does_not_reopen_until_pv_reaches_current_cap():
    engine = _engine(
        {
            CONF_MAX_INVERTER_POWER: 4400,
            CONF_INVERTER_EXPORT_LIMIT: 80,
            CONF_INVERTER_EXPORT_DEADBAND: 40,
        }
    )

    result = engine._calculate_inverter_derating_target(
        {
            "feedin_solar": False,
            "solar_production": 1500,
            "grid_power": -20,
            "previous_inverter_derating_target": 1800,
            "battery_analysis": {"average_soc": 98},
        }
    )

    assert result["inverter_derating_target"] == 1800
    assert "has not reached the current derating target" in result["inverter_derating_reason"]
    assert result["inverter_derating_alarm"] is False
    assert result["inverter_derating_unreached_since"] is not None


def test_inverter_derating_releases_to_max_when_cap_unused_for_minutes():
    engine = _engine(
        {
            CONF_MAX_INVERTER_POWER: 4400,
            CONF_INVERTER_EXPORT_LIMIT: 80,
            CONF_INVERTER_EXPORT_DEADBAND: 40,
        }
    )
    now = datetime.now(timezone.utc)

    result = engine._calculate_inverter_derating_target(
        {
            "feedin_solar": False,
            "solar_production": 1500,
            "grid_power": -20,
            "previous_inverter_derating_target": 1800,
            "previous_inverter_derating_unreached_since": now - timedelta(minutes=6),
            "inverter_derating_evaluated_at": now,
            "battery_analysis": {"average_soc": 98},
        }
    )

    assert result["inverter_derating_target"] == 4400
    assert "release the inverter to max power" in result["inverter_derating_reason"]
    assert result["inverter_derating_alarm"] is False
    assert result["inverter_derating_unreached_since"] is None


def test_inverter_derating_respects_configured_unused_release_minutes():
    engine = _engine(
        {
            CONF_MAX_INVERTER_POWER: 4400,
            CONF_INVERTER_EXPORT_LIMIT: 80,
            CONF_INVERTER_EXPORT_DEADBAND: 40,
            CONF_INVERTER_DERATING_UNUSED_RELEASE_MINUTES: 2,
        }
    )
    now = datetime.now(timezone.utc)

    result = engine._calculate_inverter_derating_target(
        {
            "feedin_solar": False,
            "solar_production": 1500,
            "grid_power": -20,
            "previous_inverter_derating_target": 1800,
            "previous_inverter_derating_unreached_since": now - timedelta(minutes=3),
            "inverter_derating_evaluated_at": now,
            "battery_analysis": {"average_soc": 98},
        }
    )

    assert result["inverter_derating_target"] == 4400
    assert "for 2 minutes" in result["inverter_derating_reason"]
    assert result["inverter_derating_alarm"] is False


def test_inverter_derating_reduces_when_export_above_band():
    engine = _engine(
        {
            CONF_MAX_INVERTER_POWER: 4400,
            CONF_INVERTER_EXPORT_LIMIT: 80,
            CONF_INVERTER_EXPORT_DEADBAND: 40,
        }
    )

    result = engine._calculate_inverter_derating_target(
        {
            "feedin_solar": False,
            "solar_production": 2200,
            "grid_power": -240,
            "battery_analysis": {"average_soc": 98},
        }
    )

    assert result["inverter_derating_target"] == 2040
    assert "reduce from current solar 2200W toward 80W export" in result["inverter_derating_reason"]
    assert result["inverter_derating_alarm"] is False


def test_inverter_derating_raises_alarm_when_low_soc_still_requires_derating():
    engine = _engine(
        {
            CONF_MAX_INVERTER_POWER: 4400,
            CONF_INVERTER_EXPORT_LIMIT: 80,
            CONF_INVERTER_EXPORT_DEADBAND: 40,
            CONF_INVERTER_DERATING_SOC_BYPASS_THRESHOLD: 95,
        }
    )

    result = engine._calculate_inverter_derating_target(
        {
            "feedin_solar": False,
            "solar_production": 2200,
            "grid_power": -260,
            "battery_analysis": {"average_soc": 40},
        }
    )

    assert result["inverter_derating_target"] == 2020
    assert result["inverter_derating_alarm"] is True
    assert "Battery SOC 40%" in result["inverter_derating_alarm_reason"]


def test_inverter_derating_bypasses_curtailment_for_low_soc_inside_tolerance():
    engine = _engine(
        {
            CONF_MAX_INVERTER_POWER: 4400,
            CONF_INVERTER_EXPORT_LIMIT: 80,
            CONF_INVERTER_EXPORT_DEADBAND: 40,
            CONF_INVERTER_DERATING_SOC_BYPASS_THRESHOLD: 95,
        }
    )

    result = engine._calculate_inverter_derating_target(
        {
            "feedin_solar": False,
            "solar_production": 2200,
            "grid_power": -100,
            "battery_analysis": {"average_soc": 40},
        }
    )

    assert result["inverter_derating_target"] == 4400
    assert "keep inverter unrestricted" in result["inverter_derating_reason"]
    assert result["inverter_derating_alarm"] is False


def test_inverter_derating_fallback_keeps_stable_house_cap_when_solar_already_below_it():
    engine = _engine(
        {
            CONF_MAX_INVERTER_POWER: 4400,
            CONF_INVERTER_EXPORT_LIMIT: 100,
        }
    )

    result = engine._calculate_inverter_derating_target(
        {
            "feedin_solar": False,
            "solar_production": 700,
            "house_consumption": 900,
            "battery_analysis": {"average_soc": 98},
        }
    )

    assert result["inverter_derating_target"] == 1000
    assert "stable fallback cap" in result["inverter_derating_reason"]
    assert result["inverter_derating_alarm"] is False




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
    engine = _engine({
        CONF_PRICE_ADJUSTMENT_MULTIPLIER: 1.0,
        CONF_PRICE_ADJUSTMENT_OFFSET: 0.0,
    })
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


def test_price_analysis_uses_interval_aware_overrides_when_available():
    """Timestamp-aware summary overrides should bypass flat transport reuse."""
    engine = _engine({
        CONF_PRICE_ADJUSTMENT_MULTIPLIER: 1.0,
        CONF_PRICE_ADJUSTMENT_OFFSET: 0.0,
    })
    analysis = engine._analyze_comprehensive_pricing(
        {
            "current_price": 0.10,
            "highest_price": 0.20,
            "lowest_price": 0.05,
            "next_price": 0.12,
            "transport_cost": 0.02,
            "price_analysis_overrides": {
                "current_price": 0.21,
                "highest_price": 0.28,
                "lowest_price": 0.14,
                "next_price": 0.17,
                "raw_current_price": 0.10,
                "raw_highest_price": 0.18,
                "raw_lowest_price": 0.09,
                "raw_next_price": 0.11,
                "transport_cost": 0.11,
            },
        }
    )

    assert analysis["current_price"] == pytest.approx(0.21)
    assert analysis["highest_price"] == pytest.approx(0.28)
    assert analysis["lowest_price"] == pytest.approx(0.14)
    assert analysis["next_price"] == pytest.approx(0.17)
    assert analysis["raw_current_price"] == pytest.approx(0.10)
    assert analysis["raw_next_price"] == pytest.approx(0.11)
    assert analysis["transport_cost"] == pytest.approx(0.11)


def test_charger_limit_enforces_restrictions():
    """When car_grid_charging=False and not solar_only, limit should be 0."""
    engine = _engine()
    price_analysis = {}
    battery_analysis = {"average_soc": 60, "max_soc_threshold": 90}
    power_allocation = {"solar_for_car": 2000}
    data = {
        "car_charging_power": 3500,
        "car_grid_charging": False,
        "car_solar_only": False,
    }

    result = engine._calculate_charger_limit(
        price_analysis,
        battery_analysis,
        power_allocation,
        data,
    )

    assert result["charger_limit"] == 0
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


def test_charger_limit_for_solar_only_car_includes_current_solar_usage():
    engine = _engine()
    battery_analysis = {"average_soc": 85, "max_soc_threshold": 90}
    power_allocation = {"solar_for_car": 0, "car_current_solar_usage": 1800}
    data = {
        "car_charging_power": 2200,
        "car_grid_charging": True,
        "car_solar_only": True,
    }

    result = engine._calculate_charger_limit(
        {},
        battery_analysis,
        power_allocation,
        data,
    )

    assert result["charger_limit"] == 1800
    assert "1800W" in result["charger_limit_reason"]


def test_charger_limit_uses_sanitized_predictive_min_soc():
    engine = _engine({"predictive_charging_min_soc": "bad"})
    battery_analysis = {"average_soc": 20, "max_soc_threshold": 90}
    data = {
        "car_charging_power": 3200,
        "car_grid_charging": True,
        "battery_grid_charging": True,
    }

    result = engine._calculate_charger_limit(
        {},
        battery_analysis,
        {"solar_for_car": 0, "car_current_solar_usage": 0},
        data,
    )

    assert result["charger_limit"] == 1350
    assert "sharing grid power with batteries" in result["charger_limit_reason"]


def test_car_high_price_uses_live_solar_fallback():
    engine = _engine()
    price_analysis = {
        "data_available": True,
        "current_price": 0.30,
        "price_threshold": 0.15,
        "very_low_price": False,
        "is_low_price": False,
    }
    power_allocation = {
        "solar_for_car": 0,
        "car_current_solar_usage": 1200,
    }
    data = {
        "car_permissive_mode_active": False,
        "car_charging": False,
        "car_grid_charging": False,
    }

    result = engine._decide_car_grid_charging(
        price_analysis,
        {"average_soc": 60, "max_soc_threshold": 90},
        power_allocation,
        data,
    )

    assert result["car_grid_charging"] is True
    assert result["car_solar_only"] is True
    assert "1200W" in result["car_grid_charging_reason"]


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


@pytest.mark.asyncio
async def test_three_phase_preserves_single_phase_logic(monkeypatch):
    """Three-phase evaluation should reuse single-phase decision logic."""
    fixed_now = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "custom_components.electricity_planner.decision_engine.dt_util.utcnow",
        lambda: fixed_now,
    )
    monkeypatch.setattr(
        "custom_components.electricity_planner.decision_engine.dt_util.now",
        lambda: fixed_now,
    )
    monkeypatch.setattr(
        "custom_components.electricity_planner.helpers.dt_util.utcnow",
        lambda: fixed_now,
    )
    monkeypatch.setattr(
        "custom_components.electricity_planner.helpers.dt_util.now",
        lambda: fixed_now,
    )

    engine = _engine()
    base_payload = {
        CONF_PHASE_MODE: PHASE_MODE_SINGLE,
        "current_price": 0.12,
        "highest_price": 0.30,
        "lowest_price": 0.05,
        "next_price": 0.10,
        "battery_soc": [{"entity_id": "sensor.battery_main", "soc": 45}],
        "solar_production": 4200,
        "house_consumption": 3200,
        "solar_surplus": 1000,
        "car_charging_power": 0,
        "monthly_grid_peak": 5000,
        "transport_cost": 0.0,
        "transport_cost_lookup": [],
        "nordpool_prices_today": None,
        "nordpool_prices_tomorrow": None,
        "car_permissive_mode_active": False,
    }

    single_phase_result = await engine.evaluate_charging_decision(dict(base_payload))

    three_phase_payload = dict(base_payload)
    three_phase_payload.update(
        {
            CONF_PHASE_MODE: PHASE_MODE_THREE,
            "phase_capacity_map": {phase: (10.0 if phase == "phase_1" else 0.0) for phase in PHASE_IDS},
            "phase_batteries": {
                "phase_1": [
                    {
                        "entity_id": "sensor.battery_main",
                        "soc": 45,
                        "capacity": 10.0,
                    }
                ],
                "phase_2": [],
                "phase_3": [],
            },
            "phase_details": {
                "phase_1": {
                    "name": "Phase 1",
                    "solar_production": 4200,
                    "house_consumption": 3200,
                    "solar_surplus": 1000,
                    "car_charging_power": 0,
                    "battery_power": None,
                    "has_car_sensor": True,
                    "has_battery_power_sensor": False,
                }
            },
        }
    )

    three_phase_result = await engine.evaluate_charging_decision(three_phase_payload)

    for key, value in single_phase_result.items():
        if key in {"phase_results", "phase_mode"}:
            continue
        assert three_phase_result[key] == value

    assert three_phase_result["phase_mode"] == PHASE_MODE_THREE
    assert three_phase_result["phase_results"]["phase_1"]["grid_setpoint"] == single_phase_result["grid_setpoint"]
    assert set(three_phase_result["phase_results"].keys()) == {"phase_1"}


def test_distribute_phase_decisions_returns_empty_without_phase_details():
    engine = _engine()
    overall = {
        "grid_setpoint": 0,
        "battery_grid_charging": False,
        "car_grid_charging": False,
        "grid_components": {"battery": 0, "car": 0},
        "battery_grid_charging_reason": "Idle",
        "car_grid_charging_reason": "Idle",
        "charger_limit": 0,
    }

    result = engine._distribute_phase_decisions(overall, {})
    assert result == {}


def test_distribute_phase_decisions_spreads_car_without_phase_sensors():
    engine = _engine()
    overall = {
        "grid_setpoint": 6000,
        "grid_components": {"battery": 0, "car": 6000},
        "battery_grid_charging": False,
        "battery_grid_charging_reason": "Battery idle",
        "car_grid_charging": True,
        "car_grid_charging_reason": "Car allowed",
        "charger_limit": 6000,
    }
    phase_details = {
        phase: {
            "name": phase.upper(),
            "solar_production": None,
            "house_consumption": None,
            "solar_surplus": None,
            "car_charging_power": None,
            "battery_power": None,
            "has_car_sensor": False,
            "has_battery_power_sensor": False,
        }
        for phase in PHASE_IDS
    }
    phase_capacity_map = {phase: 0.0 for phase in PHASE_IDS}
    phase_batteries = {phase: [] for phase in PHASE_IDS}

    result = engine._distribute_phase_decisions(
        overall,
        {
            "phase_details": phase_details,
            "phase_capacity_map": phase_capacity_map,
            "phase_batteries": phase_batteries,
        },
    )

    for phase in PHASE_IDS:
        assert result[phase]["grid_components"]["car"] == 2000
        assert result[phase]["charger_limit"] == 2000
        assert result[phase]["car_grid_charging"] is True


def test_distribute_phase_decisions_applies_capacity_weights():
    engine = _engine()
    overall = {
        "grid_setpoint": 6000,
        "grid_components": {"battery": 4000, "car": 2000},
        "battery_grid_charging": True,
        "battery_grid_charging_reason": "Batteries allowed",
        "car_grid_charging": True,
        "car_grid_charging_reason": "Car allowed",
        "charger_limit": 9000,
    }

    phase_details = {
        "phase_1": {"has_car_sensor": True, "car_charging_power": 1500},
        "phase_2": {"has_car_sensor": True, "car_charging_power": 500},
        "phase_3": {"has_car_sensor": False},
    }
    phase_capacity_map = {"phase_1": 5.0, "phase_2": 11.0, "phase_3": 0.0}
    phase_batteries = {
        "phase_1": [{"entity_id": "sensor.battery_a"}],
        "phase_2": [{"entity_id": "sensor.battery_a"}, {"entity_id": "sensor.battery_b"}],
        "phase_3": [],
    }

    result = engine._distribute_phase_decisions(
        overall,
        {
            "phase_details": phase_details,
            "phase_capacity_map": phase_capacity_map,
            "phase_batteries": phase_batteries,
        },
    )

    # Battery power: 4000W distributed by capacity (5.0 vs 11.0 kWh)
    assert result["phase_1"]["grid_components"]["battery"] == 1250
    assert result["phase_2"]["grid_components"]["battery"] == 2750
    assert result["phase_3"]["grid_components"]["battery"] == 0

    # Car power: 2000W distributed EQUALLY across phases with car sensors (not by current draw)
    # Phase 1 and Phase 2 both have car sensors, so each gets 1000W
    assert result["phase_1"]["grid_components"]["car"] == 1000
    assert result["phase_2"]["grid_components"]["car"] == 1000
    assert result["phase_3"]["grid_components"]["car"] == 0

    # Charger limit: 9000W distributed EQUALLY across car phases
    assert result["phase_1"]["charger_limit"] == 4500
    assert result["phase_2"]["charger_limit"] == 4500
    assert result["phase_3"]["charger_limit"] == 0

    assert result["phase_3"]["battery_grid_charging_reason"] == "No batteries assigned to this phase"
    assert result["phase_3"]["car_grid_charging_reason"] == "No EV feed configured for this phase"
    assert result["phase_1"]["capacity_share"] == pytest.approx(5.0 / 16.0)
    assert result["phase_1"]["capacity_share_kwh"] == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_evaluate_three_phase_wraps_single_phase(monkeypatch):
    engine = _engine()

    base_decision = {
        "grid_setpoint": 6000,
        "grid_components": {"battery": 4000, "car": 2000},
        "battery_grid_charging": True,
        "battery_grid_charging_reason": "Batteries allowed",
        "car_grid_charging": True,
        "car_grid_charging_reason": "Car allowed",
        "charger_limit": 9000,
    }

    engine._evaluate_single_phase = AsyncMock(return_value=dict(base_decision))

    phase_details = {
        "phase_1": {"has_car_sensor": True, "car_charging_power": 1500},
        "phase_2": {"has_car_sensor": True, "car_charging_power": 500},
        "phase_3": {"has_car_sensor": False},
    }
    phase_capacity_map = {"phase_1": 5.0, "phase_2": 11.0, "phase_3": 0.0}
    phase_batteries = {
        "phase_1": [{"entity_id": "sensor.battery_a"}],
        "phase_2": [{"entity_id": "sensor.battery_a"}, {"entity_id": "sensor.battery_b"}],
        "phase_3": [],
    }

    result = await engine.evaluate_charging_decision(
        {
            CONF_PHASE_MODE: PHASE_MODE_THREE,
            "phase_details": phase_details,
            "phase_capacity_map": phase_capacity_map,
            "phase_batteries": phase_batteries,
        }
    )

    engine._evaluate_single_phase.assert_awaited_once()
    assert result["phase_mode"] == PHASE_MODE_THREE
    assert result["phase_results"]["phase_1"]["grid_setpoint"] > 0
    assert result["phase_results"]["phase_3"]["grid_setpoint"] == 0


def test_car_distribution_ignores_current_draw():
    """Test that car power distribution is equal regardless of current charging power."""
    engine = _engine()
    overall = {
        "grid_setpoint": 10000,
        "grid_components": {"battery": 0, "car": 10000},
        "battery_grid_charging": False,
        "battery_grid_charging_reason": "Not charging",
        "car_grid_charging": True,
        "car_grid_charging_reason": "Car allowed",
        "charger_limit": 11000,
    }

    # Phase 1 car is currently drawing 7kW, Phase 2 car is drawing 0W
    # But allocation should still be EQUAL (5000W each)
    phase_details = {
        "phase_1": {"has_car_sensor": True, "car_charging_power": 7000},
        "phase_2": {"has_car_sensor": True, "car_charging_power": 0},
        "phase_3": {"has_car_sensor": False},
    }
    phase_capacity_map = {"phase_1": 0.0, "phase_2": 0.0, "phase_3": 0.0}
    phase_batteries = {
        "phase_1": [],
        "phase_2": [],
        "phase_3": [],
    }

    result = engine._distribute_phase_decisions(
        overall,
        {
            "phase_details": phase_details,
            "phase_capacity_map": phase_capacity_map,
            "phase_batteries": phase_batteries,
        },
    )

    # Car power should be split equally (5000W each) not by current draw
    assert result["phase_1"]["grid_components"]["car"] == 5000
    assert result["phase_2"]["grid_components"]["car"] == 5000
    assert result["phase_3"]["grid_components"]["car"] == 0

    # Charger limit should also be split equally
    assert result["phase_1"]["charger_limit"] == 5500
    assert result["phase_2"]["charger_limit"] == 5500
    assert result["phase_3"]["charger_limit"] == 0

    # Grid setpoints match car allocations (no battery)
    assert result["phase_1"]["grid_setpoint"] == 5000
    assert result["phase_2"]["grid_setpoint"] == 5000
    assert result["phase_3"]["grid_setpoint"] == 0



# ---------------------------------------------------------------------------
# Sunny Day Grid Limit (_apply_sunny_day_grid_limit) Tests
# ---------------------------------------------------------------------------


def _battery_analysis(average_soc=50.0, max_soc=90.0):
    """Create a minimal battery_analysis dict for sunny day tests."""
    return {
        "average_soc": average_soc,
        "max_soc_threshold": max_soc,
        "min_soc_threshold": 20.0,
        "batteries_full": average_soc >= max_soc,
        "remaining_capacity_percent": max_soc - average_soc,
        "batteries_available": True,
    }


def test_sunny_day_no_forecast_returns_unchanged():
    """No solar forecast in data → battery_analysis returned unchanged."""
    engine = _engine({
        CONF_MAX_SOC_THRESHOLD: 90,
        CONF_MAX_SOC_THRESHOLD_SUNNY: 50,
        CONF_SUNNY_FORECAST_THRESHOLD_KWH: 10.0,
    })
    ba = _battery_analysis()
    result = engine._apply_sunny_day_grid_limit(ba, {})
    assert result is ba  # exact same object


def test_sunny_day_feature_disabled_when_thresholds_equal():
    """Sunny threshold >= normal threshold → feature disabled."""
    engine = _engine({
        CONF_MAX_SOC_THRESHOLD: 90,
        CONF_MAX_SOC_THRESHOLD_SUNNY: 90,
        CONF_SUNNY_FORECAST_THRESHOLD_KWH: 10.0,
    })
    ba = _battery_analysis()
    result = engine._apply_sunny_day_grid_limit(ba, {"solar_forecast_production": 20.0})
    assert result is ba


def test_sunny_day_no_battery_capacity_still_uses_kwh_threshold():
    """Sunny mode now depends on configured forecast kWh threshold, not capacities."""
    engine = _engine({
        CONF_MAX_SOC_THRESHOLD: 90,
        CONF_MAX_SOC_THRESHOLD_SUNNY: 50,
        CONF_SUNNY_FORECAST_THRESHOLD_KWH: 8.0,
    })
    ba = _battery_analysis()
    result = engine._apply_sunny_day_grid_limit(ba, {"solar_forecast_production": 9.0})
    assert result is not ba
    assert result["max_soc_threshold"] == 50.0


def test_sunny_day_forecast_below_threshold_not_sunny():
    """Forecast below configured threshold → not a sunny day."""
    engine = _engine({
        CONF_MAX_SOC_THRESHOLD: 90,
        CONF_MAX_SOC_THRESHOLD_SUNNY: 50,
        CONF_SUNNY_FORECAST_THRESHOLD_KWH: 10.0,
    })
    ba = _battery_analysis()
    result = engine._apply_sunny_day_grid_limit(ba, {"solar_forecast_production": 9.0})
    assert result is ba  # not modified


def test_sunny_day_forecast_above_threshold_applies_limit():
    """Forecast >= configured threshold → sunny day, max SOC reduced."""
    engine = _engine({
        CONF_MAX_SOC_THRESHOLD: 90,
        CONF_MAX_SOC_THRESHOLD_SUNNY: 50,
        CONF_SUNNY_FORECAST_THRESHOLD_KWH: 10.0,
    })
    ba = _battery_analysis(average_soc=40.0, max_soc=90.0)
    result = engine._apply_sunny_day_grid_limit(ba, {"solar_forecast_production": 12.0})

    # Should be a different dict
    assert result is not ba
    assert result["max_soc_threshold"] == 50.0
    assert result["remaining_capacity_percent"] == 10.0  # 50 - 40
    assert result["batteries_full"] is False

    # Original should be untouched
    assert ba["max_soc_threshold"] == 90.0


def test_sunny_day_batteries_full_in_sunny_mode():
    """SOC >= sunny threshold → batteries_full should be True."""
    engine = _engine({
        CONF_MAX_SOC_THRESHOLD: 90,
        CONF_MAX_SOC_THRESHOLD_SUNNY: 50,
        CONF_SUNNY_FORECAST_THRESHOLD_KWH: 10.0,
    })
    ba = _battery_analysis(average_soc=55.0, max_soc=90.0)
    result = engine._apply_sunny_day_grid_limit(ba, {"solar_forecast_production": 15.0})

    assert result["max_soc_threshold"] == 50.0
    assert result["batteries_full"] is True
    assert result["remaining_capacity_percent"] == -5.0  # 50 - 55


def test_sunny_day_exactly_at_threshold():
    """Forecast exactly at configured threshold → should trigger sunny mode."""
    engine = _engine({
        CONF_MAX_SOC_THRESHOLD: 90,
        CONF_MAX_SOC_THRESHOLD_SUNNY: 50,
        CONF_SUNNY_FORECAST_THRESHOLD_KWH: 10.0,
    })
    ba = _battery_analysis(average_soc=30.0, max_soc=90.0)
    result = engine._apply_sunny_day_grid_limit(ba, {"solar_forecast_production": 10.0})

    assert result is not ba
    assert result["max_soc_threshold"] == 50.0


def test_sunny_day_no_average_soc():
    """If average_soc is None, batteries_full and remaining_capacity should not be set."""
    engine = _engine({
        CONF_MAX_SOC_THRESHOLD: 90,
        CONF_MAX_SOC_THRESHOLD_SUNNY: 50,
        CONF_SUNNY_FORECAST_THRESHOLD_KWH: 10.0,
    })
    ba = {"max_soc_threshold": 90.0, "batteries_available": True}
    result = engine._apply_sunny_day_grid_limit(ba, {"solar_forecast_production": 15.0})

    assert result["max_soc_threshold"] == 50.0
    assert "batteries_full" not in result
    assert "remaining_capacity_percent" not in result
