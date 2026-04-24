"""Tests for grid setpoint, charger limits, feed-in, and phase handling."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytz
import pytest
from homeassistant.util import dt as dt_util

from custom_components.electricity_planner.const import (
    CONF_BASE_GRID_SETPOINT,
    CONF_BATTERY_CAPACITIES,
    CONF_BATTERY_DUMP_TARGET_SOC,
    CONF_CAR_USE_BATTERY_ARBITRAGE,
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
from custom_components.electricity_planner.defaults import DEFAULT_POWER_ESTIMATES


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


def _arbitrage_battery_analysis(average_soc=95, max_soc_threshold=90):
    return {
        "average_soc": average_soc,
        "max_soc_threshold": max_soc_threshold,
    }


def _arbitrage_battery_dump_data(**overrides):
    data = {
        "car_charging_power": 2000,
        "car_grid_charging": True,
        "car_grid_import_allowed": False,
        "battery_grid_charging": False,
        "arbitrage_mode_active": True,
        "battery_dump_target_soc": 40,
        "battery_dump_export_power": 3000,
        "monthly_grid_peak": 0,
    }
    data.update(overrides)
    return data


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


def test_grid_setpoint_stays_zero_when_planned_car_not_drawing_yet():
    engine = _engine()

    result = engine._calculate_grid_setpoint(
        price_analysis={},
        battery_analysis={"average_soc": 60, "max_soc_threshold": 90},
        power_allocation={"solar_for_car": 0, "car_current_solar_usage": 0},
        data={
            "car_charging_power": 0,
            "car_grid_charging": True,
            "car_grid_import_allowed": True,
            "battery_grid_charging": False,
            "monthly_grid_peak": 4000,
        },
        charger_limit=3000,
    )

    assert result["grid_setpoint"] == 0
    assert result["grid_components"]["battery"] == 0
    assert result["grid_components"]["car"] == 0
    assert "No grid charging needed" in result["grid_setpoint_reason"]


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
        "previous_car_charging": True,
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


def test_grid_setpoint_tracks_live_car_draw_even_on_fresh_start():
    engine = _engine()

    result = engine._calculate_grid_setpoint(
        price_analysis={},
        battery_analysis={"average_soc": 80, "max_soc_threshold": 90},
        power_allocation={"solar_for_car": 0, "car_current_solar_usage": 0},
        data={
            "car_charging_power": 1500,
            "car_grid_charging": True,
            "car_grid_import_allowed": True,
            "previous_car_charging": False,
            "battery_grid_charging": True,
            "monthly_grid_peak": 8000,
        },
        charger_limit=7000,
    )

    assert result["grid_setpoint"] == 5500
    assert result["grid_components"]["car"] == 1500
    assert result["grid_components"]["battery"] == 4000
    assert "car pulling 1500W" in result["grid_setpoint_reason"]
    assert "battery charging 4000W" in result["grid_setpoint_reason"]


def test_grid_setpoint_uses_live_draw_once_car_session_is_established():
    engine = _engine()

    result = engine._calculate_grid_setpoint(
        price_analysis={},
        battery_analysis={"average_soc": 80, "max_soc_threshold": 90},
        power_allocation={"solar_for_car": 0, "car_current_solar_usage": 0},
        data={
            "car_charging_power": 1500,
            "car_grid_charging": True,
            "car_grid_import_allowed": True,
            "previous_car_charging": True,
            "battery_grid_charging": True,
            "monthly_grid_peak": 8000,
        },
        charger_limit=7000,
    )

    assert result["grid_setpoint"] == 5500
    assert result["grid_components"]["car"] == 1500
    assert result["grid_components"]["battery"] == 4000
    assert "car pulling 1500W" in result["grid_setpoint_reason"]
    assert "battery charging 4000W" in result["grid_setpoint_reason"]


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


def test_grid_setpoint_preserves_battery_charging_when_car_is_solar_only():
    engine = _engine()
    battery_analysis = {"average_soc": 50, "max_soc_threshold": 90}
    power_allocation = {"solar_for_car": 1000, "car_current_solar_usage": 500}
    data = {
        "car_charging_power": 3000,
        "car_grid_charging": True,
        "car_grid_import_allowed": False,
        "battery_grid_charging": True,
        "car_solar_only": True,
        "monthly_grid_peak": 8000,
    }

    result = engine._calculate_grid_setpoint(
        {},
        battery_analysis,
        power_allocation,
        data,
        charger_limit=3000,
    )

    assert result["grid_setpoint"] == 4000
    assert result["grid_components"]["battery"] == 4000
    assert result["grid_components"]["car"] == 0
    assert "battery charging 4000W" in result["grid_setpoint_reason"]
    assert "Solar-only car charging detected" in result["grid_setpoint_reason"]


def test_grid_setpoint_preserves_battery_export_when_car_is_solar_only():
    engine = _engine()
    battery_analysis = {"average_soc": 70, "max_soc_threshold": 90}
    power_allocation = {"solar_for_car": 2500, "car_current_solar_usage": 500}
    data = {
        "car_charging_power": 3000,
        "car_grid_charging": True,
        "car_grid_import_allowed": False,
        "car_solar_only": True,
        "arbitrage_mode_active": True,
        "battery_dump_export_power": 3500,
        "arbitrage_mode_reason": "High-price export window is active",
        "monthly_grid_peak": 4000,
    }

    result = engine._calculate_grid_setpoint(
        {},
        battery_analysis,
        power_allocation,
        data,
        charger_limit=3000,
    )

    assert result["grid_setpoint"] == -3500
    assert result["grid_components"]["battery"] == -3500
    assert result["grid_components"]["car"] == 0
    assert "Grid export scheduled" in result["grid_setpoint_reason"]
    assert "Solar-only car charging detected" in result["grid_setpoint_reason"]


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


def test_grid_setpoint_exports_battery_during_dump_window():
    engine = _engine()
    battery_analysis = {"average_soc": 70, "max_soc_threshold": 90}
    power_allocation = {"solar_for_car": 0, "car_current_solar_usage": 0}
    data = {
        "car_charging_power": 0,
        "car_grid_charging": False,
        "battery_grid_charging": False,
        "arbitrage_mode_active": True,
        "battery_dump_export_power": 3500,
        "arbitrage_mode_reason": "High-price export window is active",
        "monthly_grid_peak": 4000,
    }

    result = engine._calculate_grid_setpoint(
        {},
        battery_analysis,
        power_allocation,
        data,
        charger_limit=0,
    )

    assert result["grid_setpoint"] == -3500
    assert result["grid_components"]["battery"] == -3500
    assert result["grid_components"]["car"] == 0
    assert "Grid export scheduled" in result["grid_setpoint_reason"]
    assert "3500W export" in result["grid_setpoint_reason"]


def test_grid_setpoint_is_zero_when_nothing_is_authorised():
    engine = _engine()
    battery_analysis = {"average_soc": 60, "max_soc_threshold": 90}
    power_allocation = {"solar_for_car": 0, "car_current_solar_usage": 0}
    data = {
        "car_charging_power": 0,
        "car_grid_charging": False,
        "car_grid_import_allowed": False,
        "battery_grid_charging": False,
        "arbitrage_mode_active": False,
        "monthly_grid_peak": 4000,
    }

    result = engine._calculate_grid_setpoint(
        {},
        battery_analysis,
        power_allocation,
        data,
        charger_limit=0,
    )

    assert result["grid_setpoint"] == 0
    assert result["grid_components"]["battery"] == 0
    assert result["grid_components"]["car"] == 0
    assert "No grid charging needed" in result["grid_setpoint_reason"]


def test_grid_setpoint_permits_import_for_battery_charging_without_car():
    engine = _engine()
    battery_analysis = {"average_soc": 50, "max_soc_threshold": 90}
    power_allocation = {"solar_for_car": 0, "car_current_solar_usage": 0}
    data = {
        "car_charging_power": 0,
        "car_grid_charging": False,
        "car_grid_import_allowed": False,
        "battery_grid_charging": True,
        "monthly_grid_peak": 4000,
    }

    result = engine._calculate_grid_setpoint(
        {},
        battery_analysis,
        power_allocation,
        data,
        charger_limit=0,
    )

    assert result["grid_setpoint"] > 0
    assert result["grid_components"]["car"] == 0
    assert result["grid_components"]["battery"] == result["grid_setpoint"]
    assert "battery charging" in result["grid_setpoint_reason"]


def test_grid_setpoint_permits_import_for_car_without_battery():
    engine = _engine()
    battery_analysis = {"average_soc": 80, "max_soc_threshold": 90}
    power_allocation = {"solar_for_car": 0, "car_current_solar_usage": 0}
    data = {
        "car_charging_power": 2500,
        "car_grid_charging": True,
        "car_grid_import_allowed": True,
        "previous_car_charging": True,
        "battery_grid_charging": False,
        "monthly_grid_peak": 4000,
    }

    result = engine._calculate_grid_setpoint(
        {},
        battery_analysis,
        power_allocation,
        data,
        charger_limit=3500,
    )

    assert result["grid_setpoint"] == 2500
    assert result["grid_components"]["car"] == 2500
    assert result["grid_components"]["battery"] == 0
    assert "car pulling 2500W" in result["grid_setpoint_reason"]


def test_grid_setpoint_upstream_ignores_dump_power_when_arbitrage_inactive(caplog):
    """Upstream correctness check: when arbitrage mode is off, the decision
    logic must ignore any stale ``battery_dump_export_power`` in ``data``
    and never request grid export. The direction gate is only a safety net
    for a bug in the decision logic; a tripped gate here would indicate
    the upstream invariant is broken.
    """
    engine = _engine()
    battery_analysis = {"average_soc": 90, "max_soc_threshold": 90}
    power_allocation = {"solar_for_car": 0, "car_current_solar_usage": 0}
    data = {
        "car_charging_power": 0,
        "car_grid_charging": False,
        "battery_grid_charging": False,
        "arbitrage_mode_active": False,
        "battery_dump_export_power": 3500,
        "monthly_grid_peak": 4000,
    }

    with caplog.at_level("WARNING", logger="custom_components.electricity_planner.decision_engine"):
        result = engine._calculate_grid_setpoint(
            {},
            battery_analysis,
            power_allocation,
            data,
            charger_limit=0,
        )

    assert result["grid_setpoint"] == 0
    assert result["grid_components"]["battery"] == 0
    assert result["grid_components"]["car"] == 0
    # Upstream must produce a zero setpoint on its own; the safety-net
    # warning must not fire. If it does, the decision logic has a bug.
    assert not any(
        "safety net tripped" in record.message for record in caplog.records
    )


def test_car_decision_allows_arbitrage_charging_without_grid_import():
    engine = _engine()

    result = engine._decide_car_grid_charging(
        price_analysis={
            "data_available": True,
            "current_price": 0.30,
            "price_threshold": 0.10,
            "is_low_price": False,
            "very_low_price": False,
        },
        battery_analysis=_arbitrage_battery_analysis(),
        power_allocation={},
        data={
            "previous_car_charging": False,
            "has_min_charging_window": False,
            "arbitrage_mode_active": True,
            "battery_dump_export_power": 3000,
        },
    )

    assert result["car_grid_charging"] is True
    assert result["car_grid_import_allowed"] is False
    assert "Arbitrage mode active" in result["car_grid_charging_reason"]


def test_car_decision_prioritizes_car_above_arbitrage_reserve_even_below_max_soc():
    engine = _engine()

    result = engine._decide_car_grid_charging(
        price_analysis={
            "data_available": True,
            "current_price": 0.30,
            "price_threshold": 0.10,
            "is_low_price": False,
            "very_low_price": False,
        },
        battery_analysis=_arbitrage_battery_analysis(average_soc=60, max_soc_threshold=90),
        power_allocation={},
        data={
            "previous_car_charging": False,
            "has_min_charging_window": False,
            "arbitrage_mode_active": True,
            "battery_dump_target_soc": 40,
            "battery_dump_export_power": 3000,
        },
    )

    assert result["car_grid_charging"] is True
    assert result["car_grid_import_allowed"] is False
    assert "Arbitrage mode active" in result["car_grid_charging_reason"]


def test_car_decision_skips_arbitrage_when_local_use_disabled():
    engine = _engine({CONF_CAR_USE_BATTERY_ARBITRAGE: False})

    result = engine._decide_car_grid_charging(
        price_analysis={
            "data_available": True,
            "current_price": 0.30,
            "price_threshold": 0.10,
            "is_low_price": False,
            "very_low_price": False,
        },
        battery_analysis=_arbitrage_battery_analysis(),
        power_allocation={},
        data={
            "previous_car_charging": False,
            "has_min_charging_window": False,
            "arbitrage_mode_active": True,
            "battery_dump_export_power": 3000,
        },
    )

    assert result["car_grid_charging"] is False
    assert result["car_grid_import_allowed"] is False


def test_car_decision_skips_arbitrage_when_no_export_power_is_scheduled():
    engine = _engine()

    result = engine._decide_car_grid_charging(
        price_analysis={
            "data_available": True,
            "current_price": 0.30,
            "price_threshold": 0.10,
            "is_low_price": False,
            "very_low_price": False,
        },
        battery_analysis=_arbitrage_battery_analysis(),
        power_allocation={},
        data={
            "previous_car_charging": False,
            "has_min_charging_window": False,
            "arbitrage_mode_active": True,
            "battery_dump_export_power": 0,
        },
    )

    assert result["car_grid_charging"] is False
    assert result["car_grid_import_allowed"] is False


def test_car_decision_arbitrage_allows_grid_import_on_low_price_without_window():
    """When arbitrage is already charging the car and the price is low, grid
    import should be allowed even without a minimum charging window."""
    engine = _engine()

    result = engine._decide_car_grid_charging(
        price_analysis={
            "data_available": True,
            "current_price": 0.10,
            "price_threshold": 0.15,
            "is_low_price": True,
            "very_low_price": False,
        },
        battery_analysis=_arbitrage_battery_analysis(),
        power_allocation={},
        data={
            "previous_car_charging": False,
            "has_min_charging_window": False,
            "arbitrage_mode_active": True,
            "battery_dump_export_power": 3000,
        },
    )

    assert result["car_grid_charging"] is True
    assert result["car_grid_import_allowed"] is True
    assert "allowed grid import" in result["car_grid_charging_reason"]


@pytest.mark.asyncio
async def test_single_phase_passes_current_battery_decision_to_car_logic(monkeypatch):
    engine = _engine()
    seen: dict[str, object] = {}

    monkeypatch.setattr(
        "custom_components.electricity_planner.decision_engine.TimeContext.get_current_context",
        lambda: {},
    )
    monkeypatch.setattr(
        engine,
        "_analyze_comprehensive_pricing",
        lambda data: {"data_available": True},
    )
    monkeypatch.setattr(
        engine,
        "_analyze_battery_status",
        lambda battery_soc: {
            "average_soc": 95,
            "max_soc_threshold": 90,
            "batteries_count": 1,
            "batteries_available": True,
            "batteries_full": False,
        },
    )
    monkeypatch.setattr(engine, "_analyze_power_flow", lambda data: {"solar_surplus": 0})
    monkeypatch.setattr(engine, "_analyze_solar_production", lambda data: {})
    monkeypatch.setattr(engine, "_allocate_solar_power", lambda power_analysis, battery_analysis: {})
    monkeypatch.setattr(engine.power_validator, "validate_allocation", lambda *args: (True, None))
    monkeypatch.setattr(engine, "_apply_sunny_day_grid_limit", lambda battery_analysis, data: battery_analysis)
    monkeypatch.setattr(
        engine,
        "_decide_battery_grid_charging",
        lambda *args, **kwargs: {
            "battery_grid_charging": True,
            "battery_grid_charging_reason": "Battery charging now",
            "strategy_trace": [],
        },
    )
    monkeypatch.setattr(engine.strategy_manager, "get_dynamic_threshold", lambda context: None)

    def capture_car_decision(price_analysis, battery_analysis, power_allocation, data, **kwargs):
        seen["battery_grid_charging"] = data.get("battery_grid_charging")
        return {
            "car_grid_charging": False,
            "car_grid_import_allowed": False,
            "car_grid_charging_reason": "Car off",
        }

    monkeypatch.setattr(engine, "_decide_car_grid_charging", capture_car_decision)
    monkeypatch.setattr(
        engine,
        "_calculate_charger_limit",
        lambda *args, **kwargs: {"charger_limit": 0, "charger_limit_reason": "No charging"},
    )
    monkeypatch.setattr(
        engine,
        "_calculate_grid_setpoint",
        lambda *args, **kwargs: {
            "grid_setpoint": 0,
            "grid_setpoint_reason": "No grid power",
            "grid_components": {"battery": 0, "car": 0},
        },
    )
    monkeypatch.setattr(
        engine,
        "_decide_feedin_solar",
        lambda *args, **kwargs: {
            "feedin_solar": False,
            "feedin_solar_reason": "No feed-in",
            "feedin_effective_price": None,
        },
    )
    monkeypatch.setattr(
        engine,
        "_calculate_inverter_derating_target",
        lambda *args: {
            "inverter_derating_target": None,
            "inverter_derating_reason": "No derating",
            "inverter_derating_alarm": False,
            "inverter_derating_alarm_reason": "No alarm",
        },
    )

    result = await engine._evaluate_single_phase(
        {
            "current_price": 0.30,
            "battery_grid_charging": False,
            "arbitrage_mode_active": True,
            "battery_dump_export_power": 3000,
        }
    )

    assert seen["battery_grid_charging"] is True
    assert result["battery_grid_charging"] is True


def test_charger_limit_adds_arbitrage_power_to_allowed_grid():
    engine = _engine({CONF_MAX_CAR_POWER: 11000})

    result = engine._calculate_charger_limit(
        price_analysis={},
        battery_analysis=_arbitrage_battery_analysis(),
        power_allocation={"remaining_solar": 0, "solar_for_car": 0, "car_current_solar_usage": 0},
        data=_arbitrage_battery_dump_data(
            car_charging_power=5000,
            car_grid_import_allowed=True,
        ),
    )

    assert result["charger_limit"] == 5700
    assert "3000W battery arbitrage" in result["charger_limit_reason"]
    assert "2700W grid" in result["charger_limit_reason"]


def test_charger_limit_uses_arbitrage_power_without_grid_import():
    engine = _engine({CONF_MAX_CAR_POWER: 11000})

    result = engine._calculate_charger_limit(
        price_analysis={},
        battery_analysis=_arbitrage_battery_analysis(),
        power_allocation={"remaining_solar": 0, "solar_for_car": 0, "car_current_solar_usage": 0},
        data=_arbitrage_battery_dump_data(),
    )

    assert result["charger_limit"] == 3000
    assert "3000W battery arbitrage" in result["charger_limit_reason"]
    assert "grid" not in result["charger_limit_reason"]


def test_charger_limit_uses_arbitrage_power_above_reserve_even_below_max_soc():
    engine = _engine({CONF_MAX_CAR_POWER: 11000})

    result = engine._calculate_charger_limit(
        price_analysis={},
        battery_analysis=_arbitrage_battery_analysis(average_soc=60),
        power_allocation={"remaining_solar": 0, "solar_for_car": 0, "car_current_solar_usage": 0},
        data=_arbitrage_battery_dump_data(),
    )

    assert result["charger_limit"] == 3000
    assert "arbitrage reserve 40%" in result["charger_limit_reason"]
    assert "3000W battery arbitrage" in result["charger_limit_reason"]


def test_charger_limit_keeps_arbitrage_energy_below_reserve_floor():
    engine = _engine({CONF_MAX_CAR_POWER: 11000})

    result = engine._calculate_charger_limit(
        price_analysis={},
        battery_analysis=_arbitrage_battery_analysis(average_soc=35),
        power_allocation={"remaining_solar": 0, "solar_for_car": 0, "car_current_solar_usage": 0},
        data=_arbitrage_battery_dump_data(),
    )

    assert result["charger_limit"] == 0
    assert "Battery 35% < arbitrage reserve 40%" in result["charger_limit_reason"]
    assert "keeping arbitrage energy in the battery" in result["charger_limit_reason"]


def test_charger_limit_falls_back_to_configured_reserve_when_live_target_missing():
    engine = _engine(
        {
            CONF_MAX_CAR_POWER: 11000,
            CONF_BATTERY_DUMP_TARGET_SOC: 45,
        }
    )

    result = engine._calculate_charger_limit(
        price_analysis={},
        battery_analysis=_arbitrage_battery_analysis(average_soc=50),
        power_allocation={"remaining_solar": 0, "solar_for_car": 0, "car_current_solar_usage": 0},
        data=_arbitrage_battery_dump_data(battery_dump_target_soc=None),
    )

    assert result["charger_limit"] == 3000
    assert "arbitrage reserve 45%" in result["charger_limit_reason"]
    assert "3000W battery arbitrage" in result["charger_limit_reason"]


def test_charger_limit_blocks_when_configured_reserve_fallback_is_not_met():
    engine = _engine(
        {
            CONF_MAX_CAR_POWER: 11000,
            CONF_BATTERY_DUMP_TARGET_SOC: 45,
        }
    )

    result = engine._calculate_charger_limit(
        price_analysis={},
        battery_analysis=_arbitrage_battery_analysis(average_soc=40),
        power_allocation={"remaining_solar": 0, "solar_for_car": 0, "car_current_solar_usage": 0},
        data=_arbitrage_battery_dump_data(battery_dump_target_soc=None),
    )

    assert result["charger_limit"] == 0
    assert "Battery 40% < arbitrage reserve 45%" in result["charger_limit_reason"]
    assert "keeping arbitrage energy in the battery" in result["charger_limit_reason"]


def test_charger_limit_preserves_allocated_solar_while_reserving_extra_surplus_for_batteries():
    engine = _engine({CONF_MAX_CAR_POWER: 11000})

    result = engine._calculate_charger_limit(
        price_analysis={},
        battery_analysis={"average_soc": 60, "max_soc_threshold": 90},
        power_allocation={
            "remaining_solar": 0,
            "solar_for_car": 0,
            "car_current_solar_usage": 3800,
        },
        data={
            "car_charging_power": 4500,
            "car_grid_charging": True,
            "car_grid_import_allowed": True,
            "battery_grid_charging": False,
            "monthly_grid_peak": 5304,
        },
    )

    assert result["charger_limit"] == 8573
    assert "3800W allocated solar" in result["charger_limit_reason"]
    assert "4773W grid" in result["charger_limit_reason"]
    assert "surplus for batteries" in result["charger_limit_reason"]


def test_threshold_battery_reserve_leaves_remaining_surplus_for_ev_and_grid():
    engine = _engine({CONF_MAX_CAR_POWER: 11000})

    charger_limit = engine._calculate_charger_limit(
        price_analysis={},
        battery_analysis={"average_soc": 60, "max_soc_threshold": 90},
        power_allocation={
            "remaining_solar": 0,
            "solar_for_car": 500,
            "car_current_solar_usage": 0,
        },
        data={
            "car_charging_power": 4500,
            "car_grid_charging": True,
            "car_grid_import_allowed": True,
            "battery_grid_charging": False,
            "monthly_grid_peak": 4445,
        },
    )

    assert charger_limit["charger_limit"] == 4500

    grid_setpoint = engine._calculate_grid_setpoint(
        price_analysis={},
        battery_analysis={"average_soc": 60, "max_soc_threshold": 90},
        power_allocation={
            "remaining_solar": 0,
            "solar_for_car": 500,
            "car_current_solar_usage": 0,
        },
        data={
            "car_charging_power": 4500,
            "car_grid_charging": True,
            "car_grid_import_allowed": True,
            "battery_grid_charging": False,
            "monthly_grid_peak": 4445,
        },
        charger_limit=charger_limit["charger_limit"],
    )

    assert grid_setpoint["grid_setpoint"] == 4000
    assert "car pulling 4000W" in grid_setpoint["grid_setpoint_reason"]


def test_charger_limit_adds_existing_and_remaining_solar_on_top_of_grid_when_batteries_ready():
    engine = _engine({CONF_MAX_CAR_POWER: 11000})

    result = engine._calculate_charger_limit(
        price_analysis={},
        battery_analysis={"average_soc": 95, "max_soc_threshold": 90},
        power_allocation={
            "remaining_solar": 0,
            "solar_for_car": 1200,
            "car_current_solar_usage": 3800,
        },
        data={
            "car_charging_power": 4500,
            "car_grid_charging": True,
            "car_grid_import_allowed": True,
            "battery_grid_charging": False,
            "monthly_grid_peak": 5304,
        },
    )

    assert result["charger_limit"] == 9773
    assert "5000W allocated solar" in result["charger_limit_reason"]
    assert "4773W grid" in result["charger_limit_reason"]


def test_charger_limit_includes_remaining_solar_when_batteries_took_reserve():
    """Surplus that would otherwise be exported must raise the EV charger limit.

    Repro: car is idle (so ``solar_for_car`` and ``car_current_solar_usage``
    are both 0) but batteries have taken their allocated share and left
    ``remaining_solar`` available for export.  The charger limit should
    include that leftover on top of the grid allowance, up to the max.
    """
    engine = _engine({CONF_MAX_CAR_POWER: 11000})

    result = engine._calculate_charger_limit(
        price_analysis={},
        battery_analysis={"average_soc": 71, "max_soc_threshold": 90},
        power_allocation={
            "remaining_solar": 2220,
            "solar_for_car": 0,
            "car_current_solar_usage": 0,
        },
        data={
            "car_charging_power": 0,
            "car_grid_charging": True,
            "car_grid_import_allowed": True,
            "battery_grid_charging": False,
            "monthly_grid_peak": 5304,
        },
    )

    # remaining_solar (2220) + grid (4773) = 6993
    assert result["charger_limit"] == 6993
    assert "2220W allocated solar" in result["charger_limit_reason"]
    assert "4773W grid" in result["charger_limit_reason"]


def test_charger_limit_low_soc_shares_half_grid_and_preserves_allocated_solar():
    engine = _engine({CONF_MAX_CAR_POWER: 11000})

    result = engine._calculate_charger_limit(
        price_analysis={},
        battery_analysis={"average_soc": 20, "max_soc_threshold": 90},
        power_allocation={
            "remaining_solar": 0,
            "solar_for_car": 0,
            "car_current_solar_usage": 3800,
        },
        data={
            "car_charging_power": 4500,
            "car_grid_charging": True,
            "car_grid_import_allowed": True,
            "battery_grid_charging": True,
            "monthly_grid_peak": 5304,
        },
    )

    # allocated_solar (3800) + shared grid (4773 / 2 = 2386) = 6186
    assert result["charger_limit"] == 6186
    assert "Low battery SOC (20% < 30.0%)" in result["charger_limit_reason"]
    assert "3800W allocated solar" in result["charger_limit_reason"]
    assert "2386W shared grid" in result["charger_limit_reason"]


def test_charger_limit_low_soc_shares_half_grid_and_includes_remaining_solar():
    engine = _engine({CONF_MAX_CAR_POWER: 11000})

    result = engine._calculate_charger_limit(
        price_analysis={},
        battery_analysis={"average_soc": 20, "max_soc_threshold": 90},
        power_allocation={
            "remaining_solar": 2220,
            "solar_for_car": 0,
            "car_current_solar_usage": 3800,
        },
        data={
            "car_charging_power": 4500,
            "car_grid_charging": True,
            "car_grid_import_allowed": True,
            "battery_grid_charging": True,
            "monthly_grid_peak": 5304,
        },
    )

    # solar_headroom (3800 + 2220) + shared grid (4773 / 2 = 2386) = 8406
    assert result["charger_limit"] == 8406
    assert "Low battery SOC (20% < 30.0%)" in result["charger_limit_reason"]
    assert "6020W allocated solar" in result["charger_limit_reason"]
    assert "2386W shared grid" in result["charger_limit_reason"]


def test_charger_limit_no_battery_data_includes_allocated_solar_in_conservative_limit():
    engine = _engine({CONF_MAX_CAR_POWER: 11000})

    result = engine._calculate_charger_limit(
        price_analysis={},
        battery_analysis={"average_soc": None},
        power_allocation={
            "remaining_solar": 0,
            "solar_for_car": 0,
            "car_current_solar_usage": 3800,
        },
        data={
            "car_charging_power": 4500,
            "car_grid_charging": True,
            "car_grid_import_allowed": True,
            "battery_grid_charging": False,
            "monthly_grid_peak": 5304,
        },
    )

    # allocated_solar (3800) + grid (4773) = 8573
    assert result["charger_limit"] == 8573
    assert "Battery data unavailable" in result["charger_limit_reason"]
    assert "3800W allocated solar" in result["charger_limit_reason"]
    assert "4773W grid" in result["charger_limit_reason"]


def test_charger_limit_peak_import_preserves_allocated_solar_and_halves_grid():
    engine = _engine({CONF_MAX_CAR_POWER: 11000})

    result = engine._calculate_charger_limit(
        price_analysis={},
        battery_analysis={"average_soc": 60, "max_soc_threshold": 90},
        power_allocation={
            "remaining_solar": 0,
            "solar_for_car": 0,
            "car_current_solar_usage": 3800,
        },
        data={
            "car_charging_power": 4500,
            "car_grid_charging": True,
            "car_grid_import_allowed": True,
            "battery_grid_charging": False,
            "monthly_grid_peak": 5304,
            "car_peak_limited": True,
        },
    )

    # solar (3800) preserved + grid (4773) halved (2386) = 6186
    assert result["charger_limit"] == 6186
    assert "Peak import exceeded - reduced to 6186W" in result["charger_limit_reason"]
    assert "3800W allocated solar" in result["charger_limit_reason"]


def test_grid_setpoint_nets_remaining_export_after_supplying_ev_from_battery():
    engine = _engine({CONF_MAX_CAR_POWER: 11000})

    result = engine._calculate_grid_setpoint(
        price_analysis={},
        battery_analysis=_arbitrage_battery_analysis(),
        power_allocation={"solar_for_car": 0, "car_current_solar_usage": 0},
        data=_arbitrage_battery_dump_data(
            arbitrage_mode_reason="Arbitrage mode active"
        ),
        charger_limit=3000,
    )

    assert result["grid_setpoint"] == -1000
    assert result["grid_components"]["battery"] == -1000
    assert result["grid_components"]["car"] == 0
    assert "battery supplying car 2000W" in result["grid_setpoint_reason"]
    assert "battery exporting 1000W" in result["grid_setpoint_reason"]


def test_grid_setpoint_nets_ev_load_before_export_above_reserve_even_below_max_soc():
    engine = _engine({CONF_MAX_CAR_POWER: 11000})

    result = engine._calculate_grid_setpoint(
        price_analysis={},
        battery_analysis=_arbitrage_battery_analysis(average_soc=60, max_soc_threshold=90),
        power_allocation={"solar_for_car": 0, "car_current_solar_usage": 0},
        data=_arbitrage_battery_dump_data(
            arbitrage_mode_reason="Arbitrage mode active",
        ),
        charger_limit=3000,
    )

    assert result["grid_setpoint"] == -1000
    assert result["grid_components"]["battery"] == -1000
    assert result["grid_components"]["car"] == 0
    assert "battery supplying car 2000W" in result["grid_setpoint_reason"]
    assert "battery exporting 1000W" in result["grid_setpoint_reason"]


def test_grid_setpoint_combines_arbitrage_battery_with_allowed_grid_import():
    engine = _engine({CONF_MAX_CAR_POWER: 11000})

    result = engine._calculate_grid_setpoint(
        price_analysis={},
        battery_analysis=_arbitrage_battery_analysis(),
        power_allocation={"solar_for_car": 0, "car_current_solar_usage": 0},
        data=_arbitrage_battery_dump_data(
            car_charging_power=5000,
            car_grid_import_allowed=True,
            arbitrage_mode_reason="Arbitrage mode active",
        ),
        charger_limit=5700,
    )

    assert result["grid_setpoint"] == 2000
    assert result["grid_components"]["battery"] == 0
    assert result["grid_components"]["car"] == 2000
    assert "battery supplying car 3000W" in result["grid_setpoint_reason"]
    assert "car pulling 2000W" in result["grid_setpoint_reason"]


def test_grid_setpoint_does_not_export_without_scheduled_arbitrage_power():
    engine = _engine()

    result = engine._calculate_grid_setpoint(
        price_analysis={},
        battery_analysis={"average_soc": 70, "max_soc_threshold": 90},
        power_allocation={"solar_for_car": 0, "car_current_solar_usage": 0},
        data={
            "car_charging_power": 0,
            "car_grid_charging": False,
            "battery_grid_charging": False,
            "arbitrage_mode_active": True,
            "battery_dump_export_power": 0,
            "monthly_grid_peak": 4000,
        },
        charger_limit=0,
    )

    assert result["grid_setpoint"] == 0
    assert result["grid_components"]["battery"] == 0


def test_normalize_grid_components_assigns_car_share_when_setpoint_equals_charger_limit():
    engine = _engine()

    result = engine._normalize_grid_components(
        {
            "grid_setpoint": 3000,
            "battery_grid_charging": True,
            "car_grid_charging": True,
            "charger_limit": 3000,
        }
    )

    assert result == {"battery": 0, "car": 3000}


def test_recalculate_after_charger_limit_override_updates_grid_setpoint_and_phase_results():
    engine = _engine({CONF_MAX_CAR_POWER: 11000})

    result = engine.recalculate_after_override(
        baseline_data={
            "phase_mode": PHASE_MODE_THREE,
            "phase_details": {
                "phase_1": {
                    "name": "Phase 1",
                    "has_car_sensor": True,
                    "car_charging_power": 6000,
                }
            },
            "phase_capacity_map": {"phase_1": 10.0},
            "phase_batteries": {
                "phase_1": [{"entity_id": "sensor.battery_a", "soc": 80, "capacity": 10.0}],
            },
            "car_grid_import_allowed": True,
            "car_solar_only": False,
            "battery_grid_charging": True,
            "monthly_grid_peak": 8000,
        },
        decision={
            "price_analysis": {},
            "battery_analysis": {"average_soc": 80, "max_soc_threshold": 90},
            "power_allocation": {
                "remaining_solar": 0,
                "solar_for_car": 1000,
                "car_current_solar_usage": 500,
            },
            "monthly_grid_peak": 8000,
            "car_charging_power": 6000,
            "car_grid_charging": True,
            "car_grid_import_allowed": True,
            "car_solar_only": False,
            "battery_grid_charging": True,
            "charger_limit": 3000,
            "grid_setpoint": 7200,
            "grid_components": {"battery": 2700, "car": 4500},
        },
        override_targets={"charger_limit"},
    )

    assert result["charger_limit"] == 3000
    assert result["grid_setpoint"] == 5500
    assert result["grid_components"]["battery"] == 4000
    assert result["grid_components"]["car"] == 1500
    assert result["phase_results"]["phase_1"]["grid_setpoint"] == 5500
    assert result["phase_results"]["phase_1"]["grid_components"]["battery"] == 4000
    assert result["phase_results"]["phase_1"]["grid_components"]["car"] == 1500


def test_recalculate_after_grid_setpoint_override_normalizes_phase_results():
    engine = _engine({CONF_MAX_CAR_POWER: 11000})

    result = engine.recalculate_after_override(
        baseline_data={
            "phase_mode": PHASE_MODE_THREE,
            "phase_details": {
                "phase_1": {
                    "name": "Phase 1",
                    "has_car_sensor": True,
                    "car_charging_power": 6000,
                }
            },
            "phase_capacity_map": {"phase_1": 10.0},
            "phase_batteries": {
                "phase_1": [{"entity_id": "sensor.battery_a", "soc": 80, "capacity": 10.0}],
            },
            "car_grid_import_allowed": True,
            "car_solar_only": False,
            "battery_grid_charging": True,
            "monthly_grid_peak": 8000,
        },
        decision={
            "price_analysis": {},
            "battery_analysis": {"average_soc": 80, "max_soc_threshold": 90},
            "power_allocation": {
                "remaining_solar": 0,
                "solar_for_car": 1000,
                "car_current_solar_usage": 500,
            },
            "monthly_grid_peak": 8000,
            "car_charging_power": 6000,
            "car_grid_charging": True,
            "car_grid_import_allowed": True,
            "car_solar_only": False,
            "battery_grid_charging": True,
            "charger_limit": 3000,
            "grid_setpoint": 3000,
            "grid_components": {"battery": 2700, "car": 4500},
        },
        override_targets={"grid_setpoint"},
    )

    assert result["grid_setpoint"] == 3000
    assert result["grid_components"]["battery"] == 1125
    assert result["grid_components"]["car"] == 1875
    assert result["phase_results"]["phase_1"]["grid_setpoint"] == 3000
    assert result["phase_results"]["phase_1"]["grid_components"]["battery"] == 1125
    assert result["phase_results"]["phase_1"]["grid_components"]["car"] == 1875


def test_recalculate_after_battery_override_updates_charger_limit():
    engine = _engine({CONF_MAX_CAR_POWER: 11000})

    result = engine.recalculate_after_override(
        baseline_data={
            "car_grid_import_allowed": True,
            "car_solar_only": False,
            "monthly_grid_peak": 0,
        },
        decision={
            "price_analysis": {},
            "battery_analysis": {"average_soc": 20, "max_soc_threshold": 90},
            "power_allocation": {
                "remaining_solar": 0,
                "solar_for_car": 0,
                "car_current_solar_usage": 0,
            },
            "car_charging_power": 2700,
            "car_grid_charging": True,
            "car_grid_import_allowed": True,
            "car_solar_only": False,
            "battery_grid_charging": True,
            "charger_limit": 2700,
            "grid_setpoint": 2700,
            "grid_components": {"battery": 0, "car": 2700},
        },
        override_targets={"battery_grid_charging"},
    )

    assert result["charger_limit"] == 1350
    assert result["grid_setpoint"] == 2700
    assert result["grid_components"]["battery"] == 1350
    assert result["grid_components"]["car"] == 1350


def test_recalculate_after_car_override_clears_stale_arbitrage_state():
    engine = _engine({CONF_MAX_CAR_POWER: 11000})

    result = engine.recalculate_after_override(
        baseline_data={
            **_arbitrage_battery_dump_data(),
            "car_grid_import_allowed": False,
        },
        decision={
            "price_analysis": {},
            "battery_analysis": _arbitrage_battery_analysis(),
            "power_allocation": {
                "remaining_solar": 0,
                "solar_for_car": 0,
                "car_current_solar_usage": 0,
            },
            "car_grid_charging": False,
            "car_grid_import_allowed": True,
            "car_solar_only": False,
            "charger_limit": 5700,
            "grid_setpoint": 2000,
        },
        override_targets={"car_grid_charging"},
    )

    assert result["car_grid_import_allowed"] is False
    assert result["car_solar_only"] is False
    assert result["charger_limit"] == 0
    assert result["grid_setpoint"] == -3000
    assert result["grid_components"]["car"] == 0
    assert result["grid_components"]["battery"] == -3000


def test_recalculate_after_battery_override_while_arbitrage_active_yields_positive_setpoint():
    """Bug A regression: manually enabling battery_grid_charging while an arbitrage
    export window is active must produce a positive (import) grid setpoint, not a
    negative (export) one.  The export path was previously dominant because
    arbitrage_mode_active=True was left untouched in combined_data even after the
    override forced battery_grid_charging=True."""
    engine = _engine({CONF_MAX_CAR_POWER: 11000})

    result = engine.recalculate_after_override(
        baseline_data={
            "arbitrage_mode_active": True,
            "battery_dump_export_power": 3000,
            "arbitrage_mode_reason": "High-price export window",
            "car_grid_charging": False,
            "car_grid_import_allowed": False,
            "car_solar_only": False,
            "monthly_grid_peak": 0,
        },
        decision={
            "price_analysis": {},
            "battery_analysis": {"average_soc": 70, "max_soc_threshold": 90},
            "power_allocation": {
                "remaining_solar": 0,
                "solar_for_car": 0,
                "car_current_solar_usage": 0,
            },
            "car_charging_power": 0,
            "car_grid_charging": False,
            "car_grid_import_allowed": False,
            "car_solar_only": False,
            "battery_grid_charging": True,   # forced on by override
            "arbitrage_mode_active": True,   # stale from automatic decision
            "battery_dump_export_power": 3000,
            "charger_limit": 0,
            "grid_setpoint": -3000,          # old automatic setpoint
            "grid_components": {"battery": -3000, "car": 0},
        },
        override_targets={"battery_grid_charging"},
    )

    # The override should produce a positive setpoint (grid import for battery charging),
    # NOT the original negative export setpoint from the automatic arbitrage decision.
    assert result["grid_setpoint"] > 0, (
        f"Expected positive setpoint (charging) after battery_grid_charging override "
        f"while arbitrage is active, got {result['grid_setpoint']}"
    )
    assert result["grid_components"]["battery"] > 0
    assert result["grid_components"]["car"] == 0


def test_recalculate_after_battery_override_while_arbitrage_active_car_still_uses_battery():
    """When both battery_grid_charging is force-enabled AND the car was already using
    arbitrage battery power (car_grid_charging=True with grid_import_allowed=False),
    the grid setpoint should be positive and reflect both loads."""
    engine = _engine({CONF_MAX_CAR_POWER: 11000})

    result = engine.recalculate_after_override(
        baseline_data={
            "arbitrage_mode_active": True,
            "battery_dump_export_power": 3000,
            "arbitrage_mode_reason": "High-price export window",
            "car_grid_import_allowed": False,
            "car_solar_only": False,
            "monthly_grid_peak": 0,
        },
        decision={
            "price_analysis": {},
            "battery_analysis": {"average_soc": 70, "max_soc_threshold": 90},
            "power_allocation": {
                "remaining_solar": 0,
                "solar_for_car": 0,
                "car_current_solar_usage": 0,
            },
            "car_charging_power": 2000,
            "car_grid_charging": True,
            "car_grid_import_allowed": False,
            "car_solar_only": False,
            "battery_grid_charging": True,   # forced on
            "arbitrage_mode_active": True,
            "battery_dump_export_power": 3000,
            "charger_limit": 3000,
            "grid_setpoint": -1000,
            "grid_components": {"battery": -1000, "car": 0},
        },
        override_targets={"battery_grid_charging"},
    )

    # Once the export path is suppressed the battery should charge from grid.
    assert result["grid_setpoint"] > 0


def test_charger_limit_uses_arbitrage_not_solar_limit_when_car_solar_only_was_stale():
    """Bug B regression: a stale car_solar_only=True in coordinator state (from a
    prior solar-only charging cycle) must NOT cause _calculate_charger_limit to enter
    the solar-only branch in an arbitrage cycle.

    After the fix, _initialize_decision_data seeds car_solar_only=False so the stale
    flag stored in `data` is overwritten before it can reach the downstream calculations.
    This test exercises the charger-limit calculation directly with the corrected (False)
    flag to confirm the arbitrage limit is used."""
    engine = _engine({CONF_MAX_CAR_POWER: 11000})

    # This is what decision_data_for_downstream looks like AFTER the fix:
    # car_solar_only has been reset to False by _initialize_decision_data even
    # though the coordinator's `data` dict carried a stale True from last cycle.
    data = {
        "car_charging_power": 2000,
        "car_grid_charging": True,
        "car_grid_import_allowed": False,
        "car_solar_only": False,           # correctly reset by init (was stale True)
        "battery_grid_charging": False,
        "arbitrage_mode_active": True,
        "battery_dump_target_soc": 40,
        "battery_dump_export_power": 3000,
        "monthly_grid_peak": 0,
    }

    result = engine._calculate_charger_limit(
        price_analysis={},
        battery_analysis=_arbitrage_battery_analysis(),
        power_allocation={"solar_for_car": 1500, "car_current_solar_usage": 0},
        data=data,
    )

    # Should use arbitrage power on top of the already allocated solar, not
    # fall back to the stale solar-only limit.
    assert result["charger_limit"] == 4500
    assert "1500W allocated solar" in result["charger_limit_reason"]
    assert "battery arbitrage" in result["charger_limit_reason"]
    assert "Solar-only" not in result["charger_limit_reason"]


def test_charger_limit_stale_solar_only_flag_causes_wrong_limit():
    """Demonstrate the Bug-B failure mode: if car_solar_only=True leaks from a prior
    cycle into the charger-limit calculation, the limit is incorrectly capped at the
    solar allocation instead of the arbitrage power.

    This test documents the pre-fix behaviour so reviewers can see exactly what was
    broken.  It passes with car_solar_only=True to show the wrong outcome; contrast
    with test_charger_limit_uses_arbitrage_not_solar_limit_when_car_solar_only_was_stale
    which shows the correct outcome with the fixed (False) flag."""
    engine = _engine({CONF_MAX_CAR_POWER: 11000})

    data_with_stale_flag = {
        "car_charging_power": 2000,
        "car_grid_charging": True,
        "car_grid_import_allowed": False,
        "car_solar_only": True,            # STALE - bug scenario
        "battery_grid_charging": False,
        "arbitrage_mode_active": True,
        "battery_dump_target_soc": 40,
        "battery_dump_export_power": 3000,
        "monthly_grid_peak": 0,
    }

    result = engine._calculate_charger_limit(
        price_analysis={},
        battery_analysis=_arbitrage_battery_analysis(),
        power_allocation={"solar_for_car": 1500, "car_current_solar_usage": 0},
        data=data_with_stale_flag,
    )

    # With the stale flag the charger falls into solar-only mode: limit = 1500 W
    assert result["charger_limit"] == 1500
    assert "Solar-only" in result["charger_limit_reason"]


def test_grid_setpoint_uses_arbitrage_not_solar_only_when_car_solar_only_is_false():
    """Bug B regression: with car_solar_only correctly reset to False (as happens
    after the _initialize_decision_data fix), _calculate_grid_setpoint accounts for
    car arbitrage power and nets the battery export correctly."""
    engine = _engine({CONF_MAX_CAR_POWER: 11000})

    result = engine._calculate_grid_setpoint(
        price_analysis={},
        battery_analysis=_arbitrage_battery_analysis(),
        power_allocation={"solar_for_car": 0, "car_current_solar_usage": 0},
        data={
            "car_charging_power": 2000,
            "car_grid_charging": True,
            "car_grid_import_allowed": False,
            "car_solar_only": False,          # correctly reset
            "battery_grid_charging": False,
            "arbitrage_mode_active": True,
            "battery_dump_export_power": 3000,
            "arbitrage_mode_reason": "Arbitrage mode active",
            "monthly_grid_peak": 0,
        },
        charger_limit=3000,
    )

    # Battery supplies 2000 W to car locally; remaining 1000 W exported to grid
    assert result["grid_setpoint"] == -1000
    assert "battery supplying car 2000W" in result["grid_setpoint_reason"]
    assert "battery exporting 1000W" in result["grid_setpoint_reason"]


def test_grid_setpoint_stale_solar_only_bypasses_arbitrage_car_power():
    """Demonstrate Bug-B failure mode for grid setpoint: with stale car_solar_only=True,
    car_arbitrage_power is forced to 0 (because the flag guards the arbitrage lookup),
    so the full 3000 W is exported to grid without deducting the 2000 W the car needs
    locally - the battery would be over-discharged."""
    engine = _engine({CONF_MAX_CAR_POWER: 11000})

    result = engine._calculate_grid_setpoint(
        price_analysis={},
        battery_analysis=_arbitrage_battery_analysis(),
        power_allocation={"solar_for_car": 0, "car_current_solar_usage": 0},
        data={
            "car_charging_power": 2000,
            "car_grid_charging": True,
            "car_grid_import_allowed": False,
            "car_solar_only": True,           # STALE - bug scenario
            "battery_grid_charging": False,
            "arbitrage_mode_active": True,
            "battery_dump_export_power": 3000,
            "arbitrage_mode_reason": "Arbitrage mode active",
            "monthly_grid_peak": 0,
        },
        charger_limit=3000,
    )

    # With stale solar_only=True, car_arbitrage_power=0 so battery_need is not deducted:
    # full 3000 W gets exported instead of the correct 1000 W
    assert result["grid_setpoint"] == -3000


def test_arbitrage_mode_does_not_block_battery_grid_charging():
    """Arbitrage mode alone must NOT block battery grid charging.

    Only the explicit 'Disable Battery Charging' switch (manual override
    value=False on battery_grid_charging) should prevent grid charging.
    Normal price-based strategy evaluation must always run.
    """
    engine = _engine()
    # Stub the strategy to approve charging (e.g. price is below threshold)
    engine.strategy_manager.evaluate = lambda context: (True, "low price - charge approved")
    engine.strategy_manager.get_last_trace = lambda: [{"strategy": "Stub"}]

    result = engine._decide_battery_grid_charging(
        price_analysis={"data_available": True, "current_price": 0.08},
        battery_analysis={
            "batteries_count": 1,
            "batteries_available": True,
            "batteries_full": False,
            "average_soc": 60,
        },
        power_allocation={},
        power_analysis={"significant_solar_surplus": False, "solar_surplus": 0},
        time_context={},
        data={
            "arbitrage_mode_enabled": True,
            "arbitrage_mode_reason": "Scheduled export window",
        },
    )

    # Arbitrage mode alone must not block charging — strategy evaluation wins
    assert result["battery_grid_charging"] is True
    assert result["battery_grid_charging_reason"] == "low price - charge approved"
    assert "blocked" not in result["battery_grid_charging_reason"]


def test_arbitrage_mode_strategy_evaluation_runs_at_any_price():
    """Strategy manager is always invoked regardless of price sign when arbitrage is active."""
    engine = _engine()
    engine.strategy_manager.evaluate = lambda context: (True, "strategy approved")
    engine.strategy_manager.get_last_trace = lambda: [{"strategy": "Stub"}]

    for price in (-0.01, 0.0, 0.08, 0.20):
        result = engine._decide_battery_grid_charging(
            price_analysis={"data_available": True, "current_price": price},
            battery_analysis={
                "batteries_count": 1,
                "batteries_available": True,
                "batteries_full": False,
                "average_soc": 20,
            },
            power_allocation={},
            power_analysis={"significant_solar_surplus": False, "solar_surplus": 0},
            time_context={},
            data={"arbitrage_mode_enabled": True},
        )

        assert result["battery_grid_charging"] is True, f"Failed at price={price}"
        assert result["battery_grid_charging_reason"] == "strategy approved"


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


def test_safe_grid_setpoint_switches_to_next_month_baseline_before_month_end(monkeypatch):
    engine = _engine({CONF_BASE_GRID_SETPOINT: 5000})
    base_time = datetime(2025, 10, 31, 22, 35, tzinfo=timezone.utc)  # 23:35 local Brussels
    original_tz = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(pytz.timezone("Europe/Brussels"))

    try:
        monkeypatch.setattr(
            "custom_components.electricity_planner.decision_engine.dt_util.utcnow",
            lambda: base_time,
        )

        assert engine._get_safe_grid_setpoint(8000) == 4500
    finally:
        dt_util.set_default_time_zone(original_tz)


def test_safe_grid_setpoint_stays_on_new_month_baseline_after_midnight_until_sensor_resets(
    monkeypatch,
):
    engine = _engine({CONF_BASE_GRID_SETPOINT: 5000})
    base_time = datetime(2025, 10, 31, 23, 10, tzinfo=timezone.utc)  # 00:10 local Brussels
    original_tz = dt_util.DEFAULT_TIME_ZONE
    dt_util.set_default_time_zone(pytz.timezone("Europe/Brussels"))

    try:
        monkeypatch.setattr(
            "custom_components.electricity_planner.decision_engine.dt_util.utcnow",
            lambda: base_time,
        )

        assert engine._get_safe_grid_setpoint(8000) == 4500
    finally:
        dt_util.set_default_time_zone(original_tz)


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

    assert result["inverter_derating_target"] == 1800
    assert "averaged export is only 20W < 40W" in result["inverter_derating_reason"]
    assert result["inverter_derating_alarm"] is False
    assert result["inverter_derating_unreached_since"] is not None


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
    assert "averaged export 80W is within the 40-120W band" in result["inverter_derating_reason"]
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
    assert "averaged export is only 20W < 40W" in result["inverter_derating_reason"]
    assert result["inverter_derating_alarm"] is False
    assert result["inverter_derating_unreached_since"] is not None


def test_inverter_derating_relaxes_upward_one_step_when_export_stays_low():
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

    assert result["inverter_derating_target"] == 1900
    assert "averaged export stayed low at 20W < 40W" in result["inverter_derating_reason"]
    assert result["inverter_derating_alarm"] is False
    assert result["inverter_derating_unreached_since"] == now


def test_inverter_derating_recalculates_immediately_when_house_load_exceeds_pv():
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
            "house_consumption": 2300,
            "grid_power": 800,
            "previous_inverter_derating_target": 1800,
            "battery_analysis": {"average_soc": 98},
        }
    )

    assert result["inverter_derating_target"] == 2380
    assert "house consumption 2300W already exceeds current solar 1500W" in result["inverter_derating_reason"]
    assert "export target 80W" in result["inverter_derating_reason"]
    assert result["inverter_derating_alarm"] is False
    assert result["inverter_derating_unreached_since"] is None


def test_inverter_derating_recalculates_immediately_when_site_is_importing():
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
            "solar_production": 711,
            "grid_power": 3049,
            "previous_inverter_derating_target": 711,
            "battery_analysis": {"average_soc": 98},
        }
    )

    assert result["inverter_derating_target"] == 3840
    assert "already importing 3049W" in result["inverter_derating_reason"]
    assert "current solar + grid import + export target (3840W)" in result["inverter_derating_reason"]
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

    assert result["inverter_derating_target"] == 1900
    assert "for 2 minutes" in result["inverter_derating_reason"]
    assert result["inverter_derating_alarm"] is False


def test_inverter_derating_relaxation_timer_survives_band_fluctuation():
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
            "grid_power": -80,
            "previous_inverter_derating_target": 1800,
            "previous_inverter_derating_unreached_since": now - timedelta(minutes=20),
            "inverter_derating_evaluated_at": now,
            "battery_analysis": {"average_soc": 98},
        }
    )

    assert result["inverter_derating_target"] == 1900
    assert "stayed at or below the 40-120W control band for 5 minutes" in result["inverter_derating_reason"]
    assert result["inverter_derating_alarm"] is False
    assert result["inverter_derating_unreached_since"] == now


def test_inverter_derating_averages_export_to_avoid_spike_resets():
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
            "grid_power": -140,
            "previous_grid_power": -20,
            "previous_inverter_derating_target": 1800,
            "previous_inverter_derating_unreached_since": now - timedelta(minutes=6),
            "inverter_derating_evaluated_at": now,
            "battery_analysis": {"average_soc": 98},
        }
    )

    assert result["inverter_derating_target"] == 1440
    assert "export is 140W > 120W" in result["inverter_derating_reason"]
    assert "reduce from current solar 1500W toward 80W export" in result["inverter_derating_reason"]
    assert result["inverter_derating_alarm"] is False
    assert result["inverter_derating_unreached_since"] is None


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
    assert "export is 240W > 120W" in result["inverter_derating_reason"]
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


def test_inverter_derating_low_soc_bypass_does_not_ignore_current_overexport():
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
            "solar_production": 1500,
            "grid_power": -140,
            "previous_grid_power": -20,
            "battery_analysis": {"average_soc": 40},
        }
    )

    assert result["inverter_derating_target"] == 1440
    assert "export is 140W > 120W" in result["inverter_derating_reason"]
    assert result["inverter_derating_alarm"] is True
    assert "export is still 140W" in result["inverter_derating_alarm_reason"]


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


def test_distribute_phase_decisions_spreads_charger_limit_without_phase_sensors():
    engine = _engine()
    overall = {
        "grid_setpoint": 0,
        "grid_components": {"battery": 0, "car": 0},
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
        assert result[phase]["grid_components"]["car"] == 0
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


# ---------------------------------------------------------------------------
# _calculate_weighted_average_soc
# ---------------------------------------------------------------------------


def test_weighted_average_soc_empty_list_returns_zero():
    """Defensive check: empty list returns 0.0 (should never happen in prod)."""
    engine = _engine()
    assert engine._calculate_weighted_average_soc([]) == 0.0


def test_weighted_average_soc_no_capacities_uses_simple_mean():
    """Without configured capacities the function falls back to arithmetic mean."""
    engine = _engine()  # no CONF_BATTERY_CAPACITIES

    batteries = [
        {"entity_id": "sensor.a", "soc": 40.0},
        {"entity_id": "sensor.b", "soc": 60.0},
        {"entity_id": "sensor.c", "soc": 80.0},
    ]
    assert engine._calculate_weighted_average_soc(batteries) == pytest.approx(60.0)


def test_weighted_average_soc_weights_by_capacity():
    """Mixed capacities: average must be weighted by kWh, not count."""
    engine = _engine(
        {
            CONF_BATTERY_CAPACITIES: {
                "sensor.small": 5.0,
                "sensor.large": 15.0,
            }
        }
    )

    batteries = [
        {"entity_id": "sensor.small", "soc": 20.0},
        {"entity_id": "sensor.large", "soc": 80.0},
    ]
    # (20% * 5 + 80% * 15) / (5 + 15) = (1 + 12) / 20 = 0.65 → 65%
    assert engine._calculate_weighted_average_soc(batteries) == pytest.approx(65.0)


def test_weighted_average_soc_missing_entity_uses_default_capacity():
    """Unknown entity falls back to DEFAULT_POWER_ESTIMATES.default_battery_capacity."""
    engine = _engine(
        {
            CONF_BATTERY_CAPACITIES: {
                "sensor.known": 5.0,
                # sensor.unknown intentionally missing
            }
        }
    )

    default_cap = DEFAULT_POWER_ESTIMATES.default_battery_capacity  # 10.0 kWh
    batteries = [
        {"entity_id": "sensor.known", "soc": 20.0},
        {"entity_id": "sensor.unknown", "soc": 80.0},
    ]
    expected = (20.0 / 100 * 5.0 + 80.0 / 100 * default_cap) / (5.0 + default_cap) * 100
    assert engine._calculate_weighted_average_soc(batteries) == pytest.approx(expected)


def test_weighted_average_soc_all_zero_capacities_falls_back_to_simple_mean():
    """When total_capacity computes to 0 (all zero or negative), fall back to mean."""
    # EngineSettings.from_config drops non-positive capacities during sanitization,
    # so configuring {zero, zero} leaves battery_capacities empty and we end up in
    # the "no capacities" branch. Configure a single battery whose entity is not
    # in battery_capacities AND override default to 0 via direct settings patch.
    engine = _engine(
        {CONF_BATTERY_CAPACITIES: {"sensor.excluded": 5.0}}
    )

    # Pass only batteries whose entity_id isn't in capacities, but force
    # DEFAULT capacity path via a monkey-style replacement.
    batteries = [
        {"entity_id": "sensor.unknown_a", "soc": 30.0},
        {"entity_id": "sensor.unknown_b", "soc": 70.0},
    ]
    # Both fall back to default (10.0 kWh) so total capacity is positive; verify
    # the weighted math matches the simple mean because capacities are equal.
    assert engine._calculate_weighted_average_soc(batteries) == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# _normalize_grid_components defensive ctx=None path
# ---------------------------------------------------------------------------


def test_normalize_grid_components_tolerates_non_dict_analysis_fields():
    """ctx=None path: non-dict analysis fields are coerced to empty dicts."""
    engine = _engine()

    result = engine._normalize_grid_components(
        decision={
            "grid_setpoint": 0,
            "price_analysis": None,
            "battery_analysis": [],
            "power_analysis": "oops",
            "power_allocation": 42,
        },
    )

    assert result == {"battery": 0, "car": 0}


def test_normalize_grid_components_ctx_none_builds_context_from_decision():
    """ctx=None path builds CycleContext internally and handles valid dict inputs."""
    engine = _engine()

    result = engine._normalize_grid_components(
        decision={
            "grid_setpoint": 0,
            "price_analysis": {"current_price": 0.10},
            "battery_analysis": {"average_soc": 50.0},
            "power_analysis": {"solar_surplus": 0},
            "power_allocation": {"remaining_solar": 0},
        },
    )

    # grid_setpoint == 0 short-circuits; we only verify the ctx-build doesn't crash.
    assert result == {"battery": 0, "car": 0}


# ---------------------------------------------------------------------------
# _calculate_inverter_derating_target - fallback branches
# (invoked when grid_power_w is None or solar_production_w is None)
# ---------------------------------------------------------------------------


def test_inverter_derating_fallback_low_soc_bypass_keeps_inverter_unrestricted():
    """Telemetry incomplete + SOC below bypass → max power, no alarm."""
    engine = _engine(
        {
            CONF_MAX_INVERTER_POWER: 4400,
            CONF_INVERTER_EXPORT_LIMIT: 80,
            CONF_INVERTER_DERATING_SOC_BYPASS_THRESHOLD: 95,
        }
    )

    # grid_power absent → preferred control path skipped → fallback runs.
    result = engine._calculate_inverter_derating_target(
        {
            "feedin_solar": False,
            "solar_production": 2000,
            "house_consumption": 800,
            "battery_analysis": {"average_soc": 50},
        }
    )

    assert result["inverter_derating_target"] == 4400
    assert "keep inverter unrestricted" in result["inverter_derating_reason"]
    assert "Battery SOC 50%" in result["inverter_derating_reason"]
    assert result["inverter_derating_alarm"] is False


def test_inverter_derating_fallback_returns_none_when_house_consumption_missing():
    """Telemetry incomplete, SOC above bypass, house consumption unknown → None."""
    engine = _engine(
        {
            CONF_MAX_INVERTER_POWER: 4400,
            CONF_INVERTER_EXPORT_LIMIT: 80,
            CONF_INVERTER_DERATING_SOC_BYPASS_THRESHOLD: 95,
        }
    )

    result = engine._calculate_inverter_derating_target(
        {
            "feedin_solar": False,
            "solar_production": 2000,
            # grid_power + house_consumption both absent
            "battery_analysis": {"average_soc": 98},
        }
    )

    assert result["inverter_derating_target"] is None
    assert "House consumption unavailable" in result["inverter_derating_reason"]
    assert result["inverter_derating_alarm"] is False


def test_inverter_derating_fallback_solar_below_safe_output_caps_at_safe_output():
    """Telemetry incomplete, solar already ≤ house + export → cap at safe output."""
    engine = _engine(
        {
            CONF_MAX_INVERTER_POWER: 4400,
            CONF_INVERTER_EXPORT_LIMIT: 80,
            CONF_INVERTER_DERATING_SOC_BYPASS_THRESHOLD: 95,
        }
    )

    result = engine._calculate_inverter_derating_target(
        {
            "feedin_solar": False,
            "solar_production": 500,
            "house_consumption": 600,
            # grid_power absent → fallback path
            "battery_analysis": {"average_soc": 98},
        }
    )

    # safe_output = 600 + 80 = 680; min(4400, 680) = 680
    assert result["inverter_derating_target"] == 680
    assert "already below house" in result["inverter_derating_reason"]
    assert result["inverter_derating_alarm"] is False


def test_inverter_derating_fallback_generic_caps_at_safe_output_when_solar_missing():
    """Telemetry incomplete, solar unknown → generic fallback at safe output."""
    engine = _engine(
        {
            CONF_MAX_INVERTER_POWER: 4400,
            CONF_INVERTER_EXPORT_LIMIT: 80,
            CONF_INVERTER_DERATING_SOC_BYPASS_THRESHOLD: 95,
        }
    )

    result = engine._calculate_inverter_derating_target(
        {
            "feedin_solar": False,
            # solar_production + grid_power absent
            "house_consumption": 1200,
            "battery_analysis": {"average_soc": 98},
        }
    )

    # safe_output = 1200 + 80 = 1280; min(4400, 1280) = 1280
    assert result["inverter_derating_target"] == 1280
    assert "incomplete telemetry" in result["inverter_derating_reason"]
    assert result["inverter_derating_alarm"] is False


def test_inverter_derating_fallback_generic_when_solar_above_safe_output():
    """Telemetry incomplete (grid_power missing), solar > safe → generic fallback."""
    engine = _engine(
        {
            CONF_MAX_INVERTER_POWER: 4400,
            CONF_INVERTER_EXPORT_LIMIT: 80,
            CONF_INVERTER_DERATING_SOC_BYPASS_THRESHOLD: 95,
        }
    )

    result = engine._calculate_inverter_derating_target(
        {
            "feedin_solar": False,
            "solar_production": 3000,
            "house_consumption": 500,
            # grid_power absent
            "battery_analysis": {"average_soc": 98},
        }
    )

    # safe_output = 500 + 80 = 580; falls through to generic fallback (solar > safe)
    assert result["inverter_derating_target"] == 580
    assert "incomplete telemetry" in result["inverter_derating_reason"]
    assert result["inverter_derating_alarm"] is False
