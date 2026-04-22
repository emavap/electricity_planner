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
    """Very low prices without a sufficient window but no solar should not start charging."""
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
        power_allocation={"solar_for_car": 0},  # No solar available
        data={
            "previous_car_charging": False,
            "has_min_charging_window": False,
        },
    )

    assert decision["car_grid_charging"] is False
    reason = decision["car_grid_charging_reason"]
    assert "Very low price" in reason
    assert "waiting for longer window" in reason


def test_car_solar_only_when_very_low_price_no_window_but_solar_available():
    """Very low price without a sufficient grid window should fall back to solar-only when solar is allocated."""
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
        power_allocation={"solar_for_car": 2000},  # Solar IS available
        data={
            "previous_car_charging": False,
            "has_min_charging_window": False,
        },
    )

    assert decision["car_grid_charging"] is True
    assert decision.get("car_solar_only") is True
    reason = decision["car_grid_charging_reason"]
    assert "Very low price" in reason
    assert "solar power only" in reason
    assert "2000W" in reason


def test_car_solar_only_when_low_price_no_window_but_solar_available():
    """Low price without a grid window should fall back to solar-only when solar is allocated."""
    engine = _create_engine()

    price_analysis = {
        "data_available": True,
        "current_price": 0.12,
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
        power_allocation={"solar_for_car": 1500},  # Solar IS available
        data={
            "previous_car_charging": False,
            "has_min_charging_window": False,
        },
    )

    assert decision["car_grid_charging"] is True
    assert decision.get("car_solar_only") is True
    reason = decision["car_grid_charging_reason"]
    assert "solar power only" in reason
    assert "1500W" in reason


def test_car_solar_only_when_waiting_for_window_but_solar_available():
    """Car waiting for grid window should fall back to solar-only when solar is allocated."""
    engine = _create_engine()

    # Price is NOT low (above threshold), so is_low_price_flag=False,
    # and has_min_window=False → triggers the "waiting for window" path
    price_analysis = {
        "data_available": True,
        "current_price": 0.16,
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
        power_allocation={"solar_for_car": 1800},  # Solar IS available
        data={
            "previous_car_charging": False,
            "has_min_charging_window": False,
        },
    )

    # Price is above threshold → goes to _car_decision_for_high_price → solar-only
    assert decision["car_grid_charging"] is True
    assert decision.get("car_solar_only") is True
    reason = decision["car_grid_charging_reason"]
    assert "solar" in reason.lower()


def test_car_transitions_to_solar_only_when_price_rises_above_threshold():
    """Car should switch to solar-only (not stop) when price rises but solar is still allocated."""
    engine = _create_engine()

    price_analysis = {
        "data_available": True,
        "current_price": 0.22,
        "price_threshold": 0.15,
        "is_low_price": False,
        "very_low_price": False,
    }
    data = {
        "previous_car_charging": True,  # Was charging from grid
        "has_min_charging_window": True,
        "car_charging_locked_threshold": 0.15,
    }

    decision = engine._decide_car_grid_charging(
        price_analysis,
        battery_analysis={
            "average_soc": 92,
            "min_soc": 91,
            "max_soc_threshold": 90,
            "batteries_full": True,
        },
        power_allocation={"solar_for_car": 2000},  # Solar IS available
        data=data,
    )

    # Should switch to solar-only instead of stopping completely
    assert decision["car_grid_charging"] is True
    assert decision.get("car_solar_only") is True
    reason = decision["car_grid_charging_reason"]
    assert "solar power only" in reason
    assert "2000W" in reason
    # Locked threshold should be cleared since grid charging stopped
    assert data["car_charging_locked_threshold"] is None


def test_car_stops_completely_when_price_high_no_solar():
    """Car should stop (not solar-only) when price is high and no solar is available."""
    engine = _create_engine()

    price_analysis = {
        "data_available": True,
        "current_price": 0.22,
        "price_threshold": 0.15,
        "is_low_price": False,
        "very_low_price": False,
    }
    data = {
        "previous_car_charging": True,
        "has_min_charging_window": True,
        "car_charging_locked_threshold": 0.15,
    }

    decision = engine._decide_car_grid_charging(
        price_analysis,
        battery_analysis={
            "average_soc": 92,
            "min_soc": 91,
            "max_soc_threshold": 90,
            "batteries_full": True,
        },
        power_allocation={"solar_for_car": 0},  # No solar
        data=data,
    )

    assert decision["car_grid_charging"] is False
    assert decision.get("car_solar_only", False) is False
    reason = decision["car_grid_charging_reason"]
    assert "Price too high" in reason
    assert data["car_charging_locked_threshold"] is None


def test_solar_allocation_uses_surplus_above_threshold_for_car_when_battery_needs_charge():
    """Reserve the threshold slice for batteries and give the remainder to the car."""
    engine = _create_engine({"significant_solar_threshold": 1000, "max_soc_threshold_solar": 90})

    power_analysis = {"solar_surplus": 4000, "car_charging_power": 200}
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

    assert allocation["solar_for_batteries"] == 1000
    assert allocation["car_current_solar_usage"] == 200
    assert allocation["solar_for_car"] == 2800


def test_solar_allocation_to_batteries_is_not_capped_by_significant_threshold():
    """When the car is idle, batteries absorb all surplus up to their need."""
    engine = _create_engine({"significant_solar_threshold": 1500, "max_soc_threshold_solar": 90})

    power_analysis = {"solar_surplus": 4000, "car_charging_power": 0}
    battery_analysis = {
        "average_soc": 50,
        "min_soc": 48,
        "max_soc_threshold": 90,
        "batteries_full": False,
    }

    allocation = engine._allocate_solar_power(
        power_analysis=power_analysis,
        battery_analysis=battery_analysis,
    )

    assert allocation["solar_for_batteries"] == 3000
    assert allocation["solar_for_car"] == 0
    assert allocation["remaining_solar"] == 1000


def test_solar_allocated_to_car_when_batteries_full():
    """When batteries are full, the full surplus can go to the car."""
    engine = _create_engine({"significant_solar_threshold": 1500})

    power_analysis = {"solar_surplus": 4000, "car_charging_power": 200}
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

    assert allocation["solar_for_batteries"] == 0
    assert allocation["car_current_solar_usage"] == 200
    assert allocation["solar_for_car"] == 3800


def test_solar_allocation_requires_all_batteries_high():
    """The battery reserve depends on battery demand, not on a near-full gate."""
    engine = _create_engine({"significant_solar_threshold": 1500})

    power_analysis = {"solar_surplus": 5000, "car_charging_power": 200}
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

    assert allocation["solar_for_batteries"] == 0
    assert allocation["car_current_solar_usage"] == 200
    assert allocation["solar_for_car"] == 4800


def test_solar_allocation_without_battery_data_routes_surplus_to_car():
    """Without battery telemetry the active EV gets the surplus because no reserve applies."""
    engine = _create_engine({"significant_solar_threshold": 1500})

    power_analysis = {"solar_surplus": 4500, "car_charging_power": 200}
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

    assert allocation["solar_for_batteries"] == 0
    assert allocation["car_current_solar_usage"] == 200
    assert allocation["solar_for_car"] == 4300


def test_solar_allocation_all_to_batteries_when_car_idle():
    """When no car is charging, all solar is reserved for batteries up to their need."""
    engine = _create_engine({"significant_solar_threshold": 1500, "max_soc_threshold_solar": 90})

    allocation = engine._allocate_solar_power(
        power_analysis={"solar_surplus": 4000, "car_charging_power": 0},
        battery_analysis={
            "average_soc": 60,
            "min_soc": 58,
            "max_soc_threshold": 90,
            "batteries_full": False,
        },
    )

    assert allocation["solar_for_batteries"] == 3000
    assert allocation["solar_for_car"] == 0
    assert allocation["car_current_solar_usage"] == 0
    assert allocation["remaining_solar"] == 1000


def test_solar_bootstrap_offers_surplus_to_idle_car_when_batteries_full():
    """Batteries full + idle car → leftover is offered to the car so solar-only can start."""
    engine = _create_engine({"significant_solar_threshold": 1500})

    allocation = engine._allocate_solar_power(
        power_analysis={"solar_surplus": 3000, "car_charging_power": 0},
        battery_analysis={
            "average_soc": 95,
            "min_soc": 94,
            "max_soc_threshold": 90,
            "batteries_full": True,
        },
    )

    assert allocation["solar_for_batteries"] == 0
    assert allocation["car_current_solar_usage"] == 0
    assert allocation["solar_for_car"] == 3000
    assert allocation["remaining_solar"] == 0


def test_solar_bootstrap_offers_surplus_to_idle_car_when_batteries_near_full():
    """Batteries above safety margin + idle car → leftover offered to car for bootstrap."""
    engine = _create_engine({"significant_solar_threshold": 1500})

    allocation = engine._allocate_solar_power(
        power_analysis={"solar_surplus": 2500, "car_charging_power": 0},
        battery_analysis={
            "average_soc": 86,  # > 90 - 5 (safety margin) → batteries take nothing
            "min_soc": 85,      # >= 90 - 10 (soc_buffer) → bootstrap gate passes
            "max_soc_threshold": 90,
            "batteries_full": False,
        },
    )

    assert allocation["solar_for_batteries"] == 0
    assert allocation["solar_for_car"] == 2500
    assert allocation["remaining_solar"] == 0


def test_solar_bootstrap_skipped_when_one_battery_lagging():
    """If any battery is below the near-full gate, leftover stays as remaining_solar."""
    engine = _create_engine({"significant_solar_threshold": 1500, "max_soc_threshold_solar": 90})

    allocation = engine._allocate_solar_power(
        power_analysis={"solar_surplus": 5000, "car_charging_power": 0},
        battery_analysis={
            "average_soc": 92,
            "min_soc": 75,  # < 90 - 10 → bootstrap gate fails
            "max_soc_threshold": 90,
            "batteries_full": False,
        },
    )

    assert allocation["solar_for_batteries"] == 0
    assert allocation["solar_for_car"] == 0
    assert allocation["remaining_solar"] == 5000


def test_solar_allocation_below_threshold_reserves_all_surplus_for_battery():
    engine = _create_engine({"significant_solar_threshold": 1500, "max_soc_threshold_solar": 90})

    allocation = engine._allocate_solar_power(
        power_analysis={"solar_surplus": 1200, "car_charging_power": 0},
        battery_analysis={
            "average_soc": 50,
            "min_soc": 48,
            "max_soc_threshold": 90,
            "batteries_full": False,
        },
    )

    assert allocation["solar_for_batteries"] == 1200
    assert allocation["solar_for_car"] == 0
    assert allocation["remaining_solar"] == 0


def test_solar_allocation_reserves_threshold_then_leaves_remainder_for_ev():
    engine = _create_engine({"significant_solar_threshold": 1500, "max_soc_threshold_solar": 90})

    allocation = engine._allocate_solar_power(
        power_analysis={"solar_surplus": 2000, "car_charging_power": 200},
        battery_analysis={
            "average_soc": 60,
            "min_soc": 58,
            "max_soc_threshold": 90,
            "batteries_full": False,
        },
    )

    assert allocation["solar_for_batteries"] == 1500
    assert allocation["car_current_solar_usage"] == 200
    assert allocation["solar_for_car"] == 300
    assert allocation["remaining_solar"] == 0
