"""Decision-tree coverage tests for the core planner branches."""
from __future__ import annotations

from typing import Any

import pytest

from custom_components.electricity_planner.const import (
    CONF_ARBITRAGE_MODE_RESERVE_SOC,
    CONF_BASE_GRID_SETPOINT,
    CONF_CAR_USE_BATTERY_ARBITRAGE,
    CONF_FEEDIN_ADJUSTMENT_MULTIPLIER,
    CONF_FEEDIN_ADJUSTMENT_OFFSET,
    CONF_FEEDIN_PRICE_THRESHOLD,
    CONF_MAX_BATTERY_POWER,
    CONF_MAX_CAR_POWER,
    CONF_MAX_GRID_POWER,
    CONF_MIN_CAR_CHARGING_THRESHOLD,
    CONF_VERY_LOW_PRICE_THRESHOLD,
)
from custom_components.electricity_planner.decision_engine import ChargingDecisionEngine


def _engine(config: dict[str, Any] | None = None) -> ChargingDecisionEngine:
    base_config = {
        CONF_MAX_GRID_POWER: 12000,
        CONF_MAX_BATTERY_POWER: 4000,
        CONF_MAX_CAR_POWER: 7000,
        CONF_BASE_GRID_SETPOINT: 3000,
        CONF_MIN_CAR_CHARGING_THRESHOLD: 100,
        CONF_VERY_LOW_PRICE_THRESHOLD: 30,
        CONF_CAR_USE_BATTERY_ARBITRAGE: True,
        CONF_FEEDIN_PRICE_THRESHOLD: 0.05,
    }
    if config:
        base_config.update(config)
    return ChargingDecisionEngine(hass=None, config=base_config)


def _arbitrage_battery_analysis(average_soc: float = 95, max_soc_threshold: float = 90) -> dict[str, Any]:
    return {
        "average_soc": average_soc,
        "max_soc_threshold": max_soc_threshold,
    }


def _arbitrage_mode_data(**overrides: Any) -> dict[str, Any]:
    data = {
        "car_charging_power": 2000,
        "car_grid_charging": True,
        "car_grid_import_allowed": False,
        "battery_grid_charging": False,
        "arbitrage_mode_active": True,
        "arbitrage_mode_reserve_soc": 40,
        "arbitrage_mode_export_power": 3000,
        "monthly_grid_peak": 0,
        "previous_car_charging": False,
    }
    data.update(overrides)
    return data


@pytest.mark.parametrize(
    ("price_analysis", "battery_analysis", "power_analysis", "data", "expected_enabled", "reason_fragment"),
    [
        (
            {"data_available": True},
            {"batteries_count": 0},
            {"significant_solar_surplus": False, "solar_surplus": 0},
            {},
            False,
            "No battery entities configured",
        ),
        (
            {"data_available": True},
            {
                "batteries_count": 1,
                "batteries_available": False,
                "validation_status": "Battery data unavailable",
            },
            {"significant_solar_surplus": False, "solar_surplus": 0},
            {},
            False,
            "Battery data unavailable",
        ),
        (
            {"data_available": True},
            {
                "batteries_count": 1,
                "batteries_available": True,
                "batteries_full": True,
                "average_soc": 91,
                "max_soc_threshold": 90,
            },
            {"significant_solar_surplus": False, "solar_surplus": 0},
            {},
            False,
            "Battery average SOC 91% ≥ 90% threshold",
        ),
        (
            {"data_available": False},
            {
                "batteries_count": 1,
                "batteries_available": True,
                "batteries_full": False,
                "average_soc": 50,
            },
            {"significant_solar_surplus": False, "solar_surplus": 0},
            {},
            False,
            "No price data available",
        ),
        (
            # Arbitrage mode must NOT block battery grid charging — strategy runs
            {"data_available": True, "current_price": 0.08},
            {
                "batteries_count": 1,
                "batteries_available": True,
                "batteries_full": False,
                "average_soc": 50,
            },
            {"significant_solar_surplus": False, "solar_surplus": 0},
            {"arbitrage_mode_enabled": True},
            True,
            "strategy path",
        ),
        (
            {"data_available": True, "current_price": 0.01},
            {
                "batteries_count": 1,
                "batteries_available": True,
                "batteries_full": False,
                "average_soc": 60,
            },
            {"significant_solar_surplus": True, "solar_surplus": 2400},
            {},
            False,
            "waiting for free solar",
        ),
    ],
)
def test_battery_decision_tree_gateway_branches(
    price_analysis: dict[str, Any],
    battery_analysis: dict[str, Any],
    power_analysis: dict[str, Any],
    data: dict[str, Any],
    expected_enabled: bool,
    reason_fragment: str,
) -> None:
    engine = _engine()
    engine.strategy_manager.evaluate = lambda context: (True, "strategy path")
    engine.strategy_manager.get_last_trace = lambda: [{"strategy": "Stub"}]

    result = engine._decide_battery_grid_charging(
        price_analysis=price_analysis,
        battery_analysis=battery_analysis,
        power_allocation={},
        power_analysis=power_analysis,
        time_context={},
        data=data,
    )

    assert result["battery_grid_charging"] is expected_enabled
    assert reason_fragment in result["battery_grid_charging_reason"]


def test_battery_decision_tree_strategy_branch_passes_through_result() -> None:
    engine = _engine()
    captured_context: dict[str, Any] = {}

    def _evaluate(context: dict[str, Any]) -> tuple[bool, str]:
        captured_context.update(context)
        return True, "strategy path"

    engine.strategy_manager.evaluate = _evaluate
    engine.strategy_manager.get_last_trace = lambda: [{"strategy": "Stub"}]

    result = engine._decide_battery_grid_charging(
        price_analysis={"data_available": True, "current_price": 0.02, "price_threshold": 0.10},
        battery_analysis={
            "batteries_count": 1,
            "batteries_available": True,
            "batteries_full": False,
            "average_soc": 20,
        },
        power_allocation={},
        power_analysis={"significant_solar_surplus": False, "solar_surplus": 0},
        time_context={},
        data={"battery_stable_threshold": 0.12},
    )

    assert result["battery_grid_charging"] is True
    assert result["battery_grid_charging_reason"] == "strategy path"
    assert captured_context["battery_stable_threshold"] == 0.12


def test_battery_decision_holds_recent_on_state_to_avoid_flapping() -> None:
    engine = _engine()
    engine.strategy_manager.evaluate = lambda context: (False, "solar signal changed")
    engine.strategy_manager.get_last_trace = lambda: [{"strategy": "Stub"}]

    result = engine._decide_battery_grid_charging(
        price_analysis={
            "data_available": True,
            "current_price": 0.10,
            "price_threshold": 0.15,
        },
        battery_analysis={
            "batteries_count": 1,
            "batteries_available": True,
            "batteries_full": False,
            "average_soc": 45,
        },
        power_allocation={},
        power_analysis={"significant_solar_surplus": False, "solar_surplus": 0},
        time_context={},
        data={
            "previous_battery_grid_charging": True,
            "battery_grid_charging_state_age_seconds": 60,
        },
    )

    assert result["battery_grid_charging"] is True
    assert "avoid rapid cycling" in result["battery_grid_charging_reason"]
    assert result["strategy_trace"][-1]["strategy"] == "BatteryOnHold"


def test_battery_decision_does_not_hold_on_state_when_price_is_too_high() -> None:
    engine = _engine()
    engine.strategy_manager.evaluate = lambda context: (False, "price too high")
    engine.strategy_manager.get_last_trace = lambda: [{"strategy": "Stub"}]

    result = engine._decide_battery_grid_charging(
        price_analysis={
            "data_available": True,
            "current_price": 0.20,
            "price_threshold": 0.15,
        },
        battery_analysis={
            "batteries_count": 1,
            "batteries_available": True,
            "batteries_full": False,
            "average_soc": 45,
        },
        power_allocation={},
        power_analysis={"significant_solar_surplus": False, "solar_surplus": 0},
        time_context={},
        data={
            "previous_battery_grid_charging": True,
            "battery_grid_charging_state_age_seconds": 60,
        },
    )

    assert result["battery_grid_charging"] is False
    assert result["battery_grid_charging_reason"] == "price too high"


def test_battery_decision_does_not_hold_solar_priority_stop() -> None:
    engine = _engine()
    engine.strategy_manager.evaluate = lambda context: (
        False,
        "Using allocated solar power (2000W) for batteries instead of grid",
    )
    # Detection must be by priority + reason, not class name, so the test
    # uses a different class name on purpose.
    engine.strategy_manager.get_last_trace = lambda: [
        {
            "strategy": "RenamedSolarStrategy",
            "priority": 1,
            "should_charge": False,
            "reason": "Using allocated solar power (2000W) for batteries instead of grid",
        }
    ]

    result = engine._decide_battery_grid_charging(
        price_analysis={
            "data_available": True,
            "current_price": 0.10,
            "price_threshold": 0.15,
        },
        battery_analysis={
            "batteries_count": 1,
            "batteries_available": True,
            "batteries_full": False,
            "average_soc": 45,
        },
        power_allocation={"solar_for_batteries": 2000},
        power_analysis={"significant_solar_surplus": False, "solar_surplus": 2000},
        time_context={},
        data={
            "previous_battery_grid_charging": True,
            "battery_grid_charging_state_age_seconds": 60,
        },
    )

    assert result["battery_grid_charging"] is False
    assert "allocated solar power" in result["battery_grid_charging_reason"]


def test_battery_decision_uses_locked_threshold_during_hold() -> None:
    """Hold compares price against the locked threshold, not the live one.

    A SOC bump that drops the SOC-relaxed effective threshold below the
    current price must not break a hold that was entered at a higher
    threshold.
    """
    engine = _engine()
    engine.strategy_manager.evaluate = lambda context: (False, "price too high")
    engine.strategy_manager.get_last_trace = lambda: []

    result = engine._decide_battery_grid_charging(
        price_analysis={
            "data_available": True,
            # Live (post-SOC-drift) threshold would reject this price...
            "current_price": 0.16,
            "price_threshold": 0.15,
        },
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
            "previous_battery_grid_charging": True,
            "battery_grid_charging_state_age_seconds": 60,
            # ...but the threshold locked at hold entry tolerates it.
            "battery_grid_charging_locked_threshold": 0.20,
        },
    )

    assert result["battery_grid_charging"] is True
    assert "0.200€/kWh" in result["battery_grid_charging_reason"]
    assert result["strategy_trace"][-1]["strategy"] == "BatteryOnHold"


def test_battery_decision_falls_back_to_live_threshold_without_lock() -> None:
    """First cycle after a restart has no lock; live threshold is used."""
    engine = _engine()
    engine.strategy_manager.evaluate = lambda context: (False, "")
    engine.strategy_manager.get_last_trace = lambda: []

    result = engine._decide_battery_grid_charging(
        price_analysis={
            "data_available": True,
            "current_price": 0.10,
            "price_threshold": 0.15,
        },
        battery_analysis={
            "batteries_count": 1,
            "batteries_available": True,
            "batteries_full": False,
            "average_soc": 45,
        },
        power_allocation={},
        power_analysis={"significant_solar_surplus": False, "solar_surplus": 0},
        time_context={},
        data={
            "previous_battery_grid_charging": True,
            "battery_grid_charging_state_age_seconds": 60,
        },
    )

    assert result["battery_grid_charging"] is True
    assert result["strategy_trace"][-1]["strategy"] == "BatteryOnHold"


@pytest.mark.parametrize(
    ("price_analysis", "data", "expected_branch"),
    [
        (
            {"data_available": True, "current_price": 0.06, "price_threshold": 0.15, "very_low_price": True, "is_low_price": True},
            {"previous_car_charging": False, "has_min_charging_window": True},
            "very_low",
        ),
        (
            {"data_available": True, "current_price": 0.10, "price_threshold": 0.15, "very_low_price": False, "is_low_price": True},
            {"previous_car_charging": False, "has_min_charging_window": True},
            "low",
        ),
        (
            {"data_available": True, "current_price": 0.18, "price_threshold": 0.15, "very_low_price": False, "is_low_price": False},
            {"previous_car_charging": True, "has_min_charging_window": True, "car_charging_locked_threshold": 0.20},
            "low",
        ),
        (
            {"data_available": True, "current_price": 0.14, "price_threshold": 0.12, "very_low_price": False, "is_low_price": False},
            {"previous_car_charging": False, "has_min_charging_window": True, "car_permissive_mode_active": True},
            "low",
        ),
        (
            {"data_available": True, "current_price": 0.30, "price_threshold": 0.15, "very_low_price": False, "is_low_price": False},
            {"previous_car_charging": False, "has_min_charging_window": False},
            "high",
        ),
    ],
)
def test_car_decision_tree_selects_expected_top_level_branch(
    price_analysis: dict[str, Any],
    data: dict[str, Any],
    expected_branch: str,
) -> None:
    engine = _engine()
    calls: list[str] = []

    def _stub(name: str):
        def _inner(context, ctx, raw_data):
            calls.append(name)
            return {
                "car_grid_charging": name != "high",
                "car_grid_charging_reason": name,
                "car_solar_only": False,
            }
        return _inner

    engine._car_decision_for_very_low_price = _stub("very_low")
    engine._car_decision_for_low_price = _stub("low")
    engine._car_decision_for_high_price = _stub("high")

    result = engine._decide_car_grid_charging(
        price_analysis=price_analysis,
        battery_analysis={"average_soc": 90, "max_soc_threshold": 90},
        power_allocation={"solar_for_car": 0, "car_current_solar_usage": 0},
        data=data,
    )

    assert calls == [expected_branch]
    assert result["car_grid_charging_reason"] == expected_branch


def test_car_decision_tree_blocks_immediately_when_price_data_is_missing() -> None:
    engine = _engine()

    result = engine._decide_car_grid_charging(
        price_analysis={"data_available": False},
        battery_analysis={},
        power_allocation={},
        data={"previous_car_charging": False, "has_min_charging_window": False},
    )

    assert result["car_grid_charging"] is False
    assert result["car_grid_import_allowed"] is False
    assert result["car_grid_charging_reason"] == "No price data available"


@pytest.mark.parametrize(
    ("price_analysis", "expected_import_allowed"),
    [
        (
            {"data_available": True, "current_price": 0.30, "price_threshold": 0.10, "is_low_price": False, "very_low_price": False},
            False,
        ),
        (
            {"data_available": True, "current_price": 0.05, "price_threshold": 0.10, "is_low_price": True, "very_low_price": True},
            True,
        ),
    ],
)
def test_car_decision_tree_arbitrage_overlay(price_analysis: dict[str, Any], expected_import_allowed: bool) -> None:
    engine = _engine({CONF_MAX_CAR_POWER: 11000})

    result = engine._decide_car_grid_charging(
        price_analysis=price_analysis,
        battery_analysis=_arbitrage_battery_analysis(),
        power_allocation={"solar_for_car": 0, "car_current_solar_usage": 0},
        data={
            "previous_car_charging": False,
            "has_min_charging_window": True,
            "arbitrage_mode_active": True,
            "arbitrage_mode_export_power": 3000,
        },
    )

    assert result["car_grid_charging"] is True
    assert result["car_grid_import_allowed"] is expected_import_allowed
    assert "Arbitrage mode active" in result["car_grid_charging_reason"]


@pytest.mark.parametrize(
    ("price_analysis", "power_allocation", "expected_enabled", "reason_fragment", "expected_effective_price"),
    [
        (
            {"data_available": False},
            {"remaining_solar": 500},
            False,
            "No price data available",
            None,
        ),
        (
            {"data_available": True, "current_price": None},
            {"remaining_solar": 500},
            False,
            "No adjusted price available for feed-in",
            None,
        ),
        (
            {"data_available": True, "current_price": 0.03, "raw_current_price": 0.03},
            {"remaining_solar": 500},
            False,
            "disable solar export",
            0.03,
        ),
        (
            {"data_available": True, "current_price": 0.08, "raw_current_price": 0.08},
            {"remaining_solar": 500},
            True,
            "enable solar export",
            0.08,
        ),
    ],
)
def test_feedin_decision_tree_gateways(
    price_analysis: dict[str, Any],
    power_allocation: dict[str, Any],
    expected_enabled: bool,
    reason_fragment: str,
    expected_effective_price: float | None,
) -> None:
    engine = _engine(
        {
            CONF_FEEDIN_ADJUSTMENT_MULTIPLIER: 1.0,
            CONF_FEEDIN_ADJUSTMENT_OFFSET: 0.0,
        }
    )

    result = engine._decide_feedin_solar(price_analysis, power_allocation)

    assert result["feedin_solar"] is expected_enabled
    assert reason_fragment in result["feedin_solar_reason"]
    if expected_effective_price is None:
        assert result["feedin_effective_price"] is None
    else:
        assert result["feedin_effective_price"] == pytest.approx(expected_effective_price)


def test_feedin_decision_tree_uses_feed_specific_adjustments() -> None:
    engine = _engine(
        {
            CONF_FEEDIN_ADJUSTMENT_MULTIPLIER: 0.7,
            CONF_FEEDIN_ADJUSTMENT_OFFSET: -0.01,
            CONF_FEEDIN_PRICE_THRESHOLD: 0.02,
        }
    )

    result = engine._decide_feedin_solar(
        {"data_available": True, "current_price": 0.05, "raw_current_price": 0.05},
        {"remaining_solar": 800},
    )

    assert result["feedin_solar"] is True
    assert result["feedin_effective_price"] == pytest.approx(0.025)
    assert "0.020€/kWh" in result["feedin_solar_reason"]


@pytest.mark.parametrize(
    ("config", "battery_analysis", "power_allocation", "data", "expected_limit", "reason_fragment"),
    [
        (
            {},
            {"average_soc": 60, "max_soc_threshold": 90},
            {"solar_for_car": 2000},
            {"car_charging_power": 3500, "car_grid_charging": False, "car_solar_only": False},
            0,
            "Car grid charging not allowed",
        ),
        (
            {},
            {"average_soc": 85, "max_soc_threshold": 90},
            {"solar_for_car": 2500},
            {"car_charging_power": 3000, "car_grid_charging": True, "car_solar_only": True},
            2500,
            "Solar-only car charging",
        ),
        (
            {},
            {"average_soc": None},
            {"solar_for_car": 0, "car_current_solar_usage": 0},
            {"car_charging_power": 500, "car_grid_charging": True, "car_grid_import_allowed": True, "battery_grid_charging": False, "monthly_grid_peak": 4000},
            3600,
            "Battery data unavailable - conservative limit",
        ),
        (
            {},
            {"average_soc": 20, "max_soc_threshold": 90},
            {"solar_for_car": 0, "car_current_solar_usage": 0},
            {"car_charging_power": 3200, "car_grid_charging": True, "car_grid_import_allowed": True, "battery_grid_charging": True},
            1350,
            "sharing grid power with batteries",
        ),
        (
            {},
            {"average_soc": 60, "max_soc_threshold": 90},
            {"solar_for_car": 0, "car_current_solar_usage": 0},
            {"car_charging_power": 3200, "car_grid_charging": True, "car_grid_import_allowed": True, "battery_grid_charging": False},
            2700,
            "surplus for batteries",
        ),
        (
            {CONF_MAX_CAR_POWER: 11000},
            _arbitrage_battery_analysis(),
            {"remaining_solar": 0, "solar_for_car": 0, "car_current_solar_usage": 0},
            _arbitrage_mode_data(car_charging_power=5000, car_grid_import_allowed=True),
            5700,
            "3000W battery arbitrage",
        ),
    ],
)
def test_charger_limit_decision_tree_branches(
    config: dict[str, Any],
    battery_analysis: dict[str, Any],
    power_allocation: dict[str, Any],
    data: dict[str, Any],
    expected_limit: int,
    reason_fragment: str,
) -> None:
    engine = _engine(config)

    result = engine._calculate_charger_limit(
        price_analysis={},
        battery_analysis=battery_analysis,
        power_allocation=power_allocation,
        data=data,
    )

    assert result["charger_limit"] == expected_limit
    assert reason_fragment in result["charger_limit_reason"]


@pytest.mark.parametrize(
    ("battery_analysis", "power_allocation", "data", "charger_limit", "expected_setpoint", "expected_components", "reason_fragment"),
    [
        (
            {"average_soc": None},
            {"solar_for_car": 0, "car_current_solar_usage": 0},
            {
                "car_charging_power": 0,
                "car_grid_charging": True,
                "car_grid_import_allowed": True,
                "battery_grid_charging": False,
                "monthly_grid_peak": 4000,
                "previous_car_charging": False,
            },
            3000,
            0,
            {"battery": 0, "car": 0},
            "no grid power allocated",
        ),
        (
            {"average_soc": 50, "max_soc_threshold": 90},
            {"solar_for_car": 1000, "car_current_solar_usage": 500},
            {
                "car_charging_power": 3000,
                "car_grid_charging": True,
                "car_grid_import_allowed": False,
                "battery_grid_charging": True,
                "car_solar_only": True,
                "monthly_grid_peak": 8000,
                "previous_car_charging": True,
            },
            3000,
            4000,
            {"battery": 4000, "car": 0},
            "battery charging 4000W",
        ),
        (
            {"average_soc": 70, "max_soc_threshold": 90},
            {"solar_for_car": 0, "car_current_solar_usage": 0},
            {
                "car_charging_power": 0,
                "car_grid_charging": False,
                "battery_grid_charging": False,
                "arbitrage_mode_active": True,
                "arbitrage_mode_export_power": 3500,
                "arbitrage_mode_reason": "High-price export window is active",
                "monthly_grid_peak": 4000,
            },
            0,
            -3500,
            {"battery": -3500, "car": 0},
            "Grid export scheduled",
        ),
        (
            {"average_soc": 80, "max_soc_threshold": 90},
            {"solar_for_car": 1000, "car_current_solar_usage": 500},
            {
                "car_charging_power": 6000,
                "car_grid_charging": True,
                "car_grid_import_allowed": True,
                "battery_grid_charging": True,
                "monthly_grid_peak": 8000,
                "previous_car_charging": True,
            },
            7000,
            7200,
            {"battery": 2700, "car": 4500},
            "car pulling 4500W",
        ),
        (
            _arbitrage_battery_analysis(),
            {"solar_for_car": 0, "car_current_solar_usage": 0},
            _arbitrage_mode_data(
                car_charging_power=5000,
                car_grid_import_allowed=True,
                arbitrage_mode_reason="Arbitrage mode active",
                previous_car_charging=True,
            ),
            5700,
            2000,
            {"battery": 0, "car": 2000},
            "battery supplying car 3000W",
        ),
    ],
)
def test_grid_setpoint_decision_tree_branches(
    battery_analysis: dict[str, Any],
    power_allocation: dict[str, Any],
    data: dict[str, Any],
    charger_limit: int,
    expected_setpoint: int,
    expected_components: dict[str, int],
    reason_fragment: str,
) -> None:
    engine = _engine({CONF_MAX_CAR_POWER: 11000})

    result = engine._calculate_grid_setpoint(
        price_analysis={},
        battery_analysis=battery_analysis,
        power_allocation=power_allocation,
        data=data,
        charger_limit=charger_limit,
    )

    assert result["grid_setpoint"] == expected_setpoint
    assert result["grid_components"] == expected_components
    assert reason_fragment in result["grid_setpoint_reason"]


@pytest.mark.parametrize(
    ("battery_grid_charging", "car_grid_charging", "car_grid_import_allowed", "arbitrage_mode_active", "expected_limit", "expected_setpoint"),
    [
        (True, True, True, False, 2700, 2700),
        (False, True, False, True, 3000, -1000),
        (False, False, False, False, 0, 0),
    ],
)
def test_decision_tree_combinations_keep_charger_limit_and_grid_setpoint_coherent(
    battery_grid_charging: bool,
    car_grid_charging: bool,
    car_grid_import_allowed: bool,
    arbitrage_mode_active: bool,
    expected_limit: int,
    expected_setpoint: int,
) -> None:
    engine = _engine({CONF_MAX_CAR_POWER: 11000, CONF_ARBITRAGE_MODE_RESERVE_SOC: 40})
    battery_analysis = _arbitrage_battery_analysis(average_soc=95 if arbitrage_mode_active else 60)
    power_allocation = {"remaining_solar": 0, "solar_for_car": 0, "car_current_solar_usage": 0}
    data = {
        "car_charging_power": 2000,
        "car_grid_charging": car_grid_charging,
        "car_grid_import_allowed": car_grid_import_allowed,
        "battery_grid_charging": battery_grid_charging,
        "arbitrage_mode_active": arbitrage_mode_active,
        "arbitrage_mode_reserve_soc": 40,
        "arbitrage_mode_export_power": 3000 if arbitrage_mode_active else 0,
        "monthly_grid_peak": 0,
        "previous_car_charging": True,
    }

    charger_limit = engine._calculate_charger_limit({}, battery_analysis, power_allocation, data)["charger_limit"]
    grid_setpoint = engine._calculate_grid_setpoint({}, battery_analysis, power_allocation, data, charger_limit)

    assert charger_limit == expected_limit
    assert grid_setpoint["grid_setpoint"] == expected_setpoint
    assert grid_setpoint["grid_components"]["battery"] + grid_setpoint["grid_components"]["car"] == expected_setpoint
