"""Unit tests for car charging decisions in the decision engine."""
import pytest

from custom_components.electricity_planner.decision_engine import ChargingDecisionEngine


def _create_engine(config=None):
    """Helper to instantiate the decision engine for tests."""
    return ChargingDecisionEngine(hass=None, config=config or {})


def test_car_uses_grid_when_price_low_even_with_solar_allocation():
    """Car should keep using the grid when price is acceptable and solar is available."""
    engine = _create_engine()

    price_analysis = {
        "data_available": True,
        "current_price": 0.10,
        "price_threshold": 0.15,
        "is_low_price": True,
        "very_low_price": False,
    }
    data = {
        "previous_car_charging": False,
        "has_min_charging_window": True,
    }
    decision = engine._decide_car_grid_charging(
        price_analysis,
        battery_analysis={
            "average_soc": 92,
            "min_soc": 91,
            "max_soc_threshold": 90,
            "batteries_full": True,
        },
        power_allocation={"solar_for_car": 1800},
        data=data,
    )

    assert decision["car_grid_charging"] is True
    reason = decision["car_grid_charging_reason"]
    assert "Low price" in reason
    assert "solar" in reason
    assert "window available" in reason
    assert "car_solar_only" not in decision
    assert data["car_charging_locked_threshold"] == pytest.approx(0.15, rel=1e-6)


def test_car_limits_to_solar_only_when_price_high():
    """High price should fall back to solar-only if available."""
    engine = _create_engine()

    price_analysis = {
        "data_available": True,
        "current_price": 0.22,
        "price_threshold": 0.15,
        "is_low_price": False,
        "very_low_price": False,
    }
    decision = engine._decide_car_grid_charging(
        price_analysis,
        battery_analysis={
            "average_soc": 92,
            "min_soc": 91,
            "max_soc_threshold": 90,
            "batteries_full": True,
        },
        power_allocation={"solar_for_car": 900},
        data={"previous_car_charging": False, "has_min_charging_window": False},
    )

    assert decision["car_grid_charging"] is True
    assert decision["car_solar_only"] is True
    assert "solar power only" in decision["car_grid_charging_reason"]


def test_car_does_not_wait_for_future_price_drop():
    """Price improvements next hour should not block current low price charging."""
    engine = _create_engine()

    price_analysis = {
        "data_available": True,
        "current_price": 0.12,
        "price_threshold": 0.15,
        "is_low_price": True,
        "very_low_price": False,
        "price_trend_improving": True,
        "significant_price_drop": True,
        "next_price": 0.05,
    }
    decision = engine._decide_car_grid_charging(
        price_analysis,
        battery_analysis={
            "average_soc": 92,
            "min_soc": 91,
            "max_soc_threshold": 90,
            "batteries_full": True,
        },
        power_allocation={"solar_for_car": 0},
        data={"previous_car_charging": False, "has_min_charging_window": True},
    )

    assert decision["car_grid_charging"] is True
    assert "Low price" in decision["car_grid_charging_reason"]


def test_car_continues_when_threshold_drops_but_price_below_lock():
    """Car should continue charging if threshold drops but price still below locked value."""
    engine = _create_engine()

    price_analysis = {
        "data_available": True,
        "current_price": 0.18,
        "price_threshold": 0.15,  # new lower threshold
        "is_low_price": False,
        "very_low_price": False,
    }
    data = {
        "previous_car_charging": True,
        "has_min_charging_window": True,
        "car_charging_locked_threshold": 0.20,
    }

    decision = engine._decide_car_grid_charging(
        price_analysis,
        battery_analysis={},
        power_allocation={"solar_for_car": 0},
        data=data,
    )

    assert decision["car_grid_charging"] is True
    assert "continuing" in decision["car_grid_charging_reason"]
    assert data["car_charging_locked_threshold"] == pytest.approx(0.20, rel=1e-6)


def test_car_stops_when_price_exceeds_locked_threshold():
    """Car should stop when price rises above the locked threshold floor."""
    engine = _create_engine()

    price_analysis = {
        "data_available": True,
        "current_price": 0.23,
        "price_threshold": 0.15,
        "is_low_price": False,
        "very_low_price": False,
    }
    data = {
        "previous_car_charging": True,
        "has_min_charging_window": True,
        "car_charging_locked_threshold": 0.20,
    }

    decision = engine._decide_car_grid_charging(
        price_analysis,
        battery_analysis={},
        power_allocation={"solar_for_car": 0},
        data=data,
    )

    assert decision["car_grid_charging"] is False
    assert data["car_charging_locked_threshold"] is None
    reason = decision["car_grid_charging_reason"]
    assert "Price too high" in reason
    assert "0.230€/kWh > 0.200€/kWh" in reason


def test_car_charges_during_very_low_price_with_window():
    """Very low prices should trigger charging when minimum window is available."""
    engine = _create_engine()

    price_analysis = {
        "data_available": True,
        "current_price": 0.06,
        "price_threshold": 0.15,
        "is_low_price": True,
        "very_low_price": True,
    }
    data = {
        "previous_car_charging": False,
        "has_min_charging_window": True,
    }

    decision = engine._decide_car_grid_charging(
        price_analysis,
        battery_analysis={},
        power_allocation={"solar_for_car": 1200},
        data=data,
    )

    assert decision["car_grid_charging"] is True
    reason = decision["car_grid_charging_reason"]
    assert "Very low price" in reason
    assert "window available" in reason
    assert "solar" in reason
    assert data["car_charging_locked_threshold"] == pytest.approx(0.15, rel=1e-6)


def test_car_waits_for_window_even_with_very_low_price():
    """Very low prices without a sufficient window should not start charging."""
    engine = _create_engine()

    price_analysis = {
        "data_available": True,
        "current_price": 0.05,
        "price_threshold": 0.15,
        "is_low_price": True,
        "very_low_price": True,
    }
    decision = engine._decide_car_grid_charging(
        price_analysis,
        battery_analysis={},
        power_allocation={"solar_for_car": 2000},
        data={
            "previous_car_charging": False,
            "has_min_charging_window": False,
        },
    )

    assert decision["car_grid_charging"] is False
    reason = decision["car_grid_charging_reason"]
    assert "Very low price" in reason
    assert "waiting for longer window" in reason


def test_solar_not_allocated_to_car_until_batteries_high_soc():
    """Ensure solar allocation skips the car until batteries are nearly full."""
    engine = _create_engine()

    power_analysis = {"solar_surplus": 4000, "car_charging_power": 0}
    battery_analysis = {
        "average_soc": 60,
        "min_soc": 58,
        "max_soc_threshold": 90,
        "batteries_full": False,
    }

    allocation = engine._allocate_solar_power(
        power_analysis=power_analysis,
        battery_analysis=battery_analysis,
    )

    assert allocation["solar_for_car"] == 0


def test_solar_allocated_to_car_when_batteries_full():
    """Solar can go to the car once every battery is close to full."""
    engine = _create_engine()

    power_analysis = {"solar_surplus": 4000, "car_charging_power": 0}
    battery_analysis = {
        "average_soc": 92,
        "min_soc": 91,
        "max_soc_threshold": 90,
        "batteries_full": True,
    }

    allocation = engine._allocate_solar_power(
        power_analysis=power_analysis,
        battery_analysis=battery_analysis,
    )

    assert allocation["solar_for_car"] > 0


def test_solar_allocation_requires_all_batteries_high():
    """Solar stays with batteries if any unit is below the near-full buffer."""
    engine = _create_engine()

    power_analysis = {"solar_surplus": 5000, "car_charging_power": 0}
    battery_analysis = {
        "average_soc": 92,
        "min_soc": 75,  # One battery lagging behind
        "max_soc_threshold": 90,
        "batteries_full": False,
    }

    allocation = engine._allocate_solar_power(
        power_analysis=power_analysis,
        battery_analysis=battery_analysis,
    )

    assert allocation["solar_for_car"] == 0


def test_solar_allocation_without_battery_data_skips_car():
    """No battery telemetry means solar should not be diverted to the car."""
    engine = _create_engine()

    power_analysis = {"solar_surplus": 4500, "car_charging_power": 0}
    battery_analysis = {
        "average_soc": None,
        "min_soc": None,
        "max_soc_threshold": 90,
        "batteries_full": False,
    }

    allocation = engine._allocate_solar_power(
        power_analysis=power_analysis,
        battery_analysis=battery_analysis,
    )

    assert allocation["solar_for_car"] == 0
