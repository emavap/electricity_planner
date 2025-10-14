"""Unit tests for car charging decisions in the decision engine."""
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
    decision = engine._decide_car_grid_charging(
        price_analysis,
        battery_analysis={
            "average_soc": 92,
            "min_soc": 91,
            "max_soc_threshold": 90,
            "batteries_full": True,
        },
        power_allocation={"solar_for_car": 1800},
        data={"previous_car_charging": False, "has_min_charging_window": True},
    )

    assert decision["car_grid_charging"] is True
    reason = decision["car_grid_charging_reason"]
    assert "Low price" in reason
    assert "solar" in reason
    assert "starting" in reason
    assert "car_solar_only" not in decision


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
        price_analysis={},
        solar_forecast={},
        time_context={},
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
        price_analysis={},
        solar_forecast={},
        time_context={},
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
        price_analysis={},
        solar_forecast={},
        time_context={},
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
        price_analysis={},
        solar_forecast={},
        time_context={},
    )

    assert allocation["solar_for_car"] == 0
