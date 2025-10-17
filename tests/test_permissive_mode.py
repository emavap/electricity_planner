"""Unit tests for car permissive mode functionality."""
import pytest

from custom_components.electricity_planner.decision_engine import ChargingDecisionEngine


def _create_engine(config=None):
    """Helper to instantiate the decision engine for tests."""
    return ChargingDecisionEngine(hass=None, config=config or {})


def test_permissive_mode_increases_threshold():
    """Permissive mode should allow continuing charging at higher prices."""
    config = {
        "car_permissive_threshold_multiplier": 1.3,  # 30% increase
    }
    engine = _create_engine(config)

    price_analysis = {
        "data_available": True,
        "current_price": 0.18,  # Would normally be too high
        "price_threshold": 0.15,
        "is_low_price": False,
        "very_low_price": False,
    }

    # With permissive mode active, threshold becomes 0.15 * 1.3 = 0.195
    # Price of 0.18 should allow car to CONTINUE charging (but not start)
    data = {
        "previous_car_charging": True,  # Already charging
        "has_min_charging_window": True,
        "car_permissive_mode_active": True,  # Enable permissive mode
        "car_charging_locked_threshold": 0.15,  # Locked at start threshold
    }

    decision = engine._decide_car_grid_charging(
        price_analysis,
        battery_analysis={
            "average_soc": 50,
            "min_soc": 50,
            "max_soc_threshold": 90,
            "batteries_full": False,
        },
        power_allocation={"solar_for_car": 0},
        data=data,
    )

    assert decision["car_grid_charging"] is True
    reason = decision["car_grid_charging_reason"]
    assert "[Permissive: +30%]" in reason
    assert "continuing" in reason


def test_permissive_mode_off_uses_normal_threshold():
    """With permissive mode off, normal threshold should apply."""
    config = {
        "car_permissive_threshold_multiplier": 1.3,
    }
    engine = _create_engine(config)

    price_analysis = {
        "data_available": True,
        "current_price": 0.18,  # Above normal threshold
        "price_threshold": 0.15,
        "is_low_price": False,
        "very_low_price": False,
    }

    data = {
        "previous_car_charging": False,
        "has_min_charging_window": False,
        "car_permissive_mode_active": False,  # Permissive mode disabled
    }

    decision = engine._decide_car_grid_charging(
        price_analysis,
        battery_analysis={
            "average_soc": 50,
            "min_soc": 50,
            "max_soc_threshold": 90,
            "batteries_full": False,
        },
        power_allocation={"solar_for_car": 0},
        data=data,
    )

    assert decision["car_grid_charging"] is False
    reason = decision["car_grid_charging_reason"]
    assert "[Permissive:" not in reason
    assert "Price too high" in reason


def test_permissive_mode_shown_in_reason_when_active():
    """Reason string should show permissive mode when active."""
    config = {
        "car_permissive_threshold_multiplier": 1.2,  # 20% increase
    }
    engine = _create_engine(config)

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
        "car_permissive_mode_active": True,
    }

    decision = engine._decide_car_grid_charging(
        price_analysis,
        battery_analysis={
            "average_soc": 50,
            "min_soc": 50,
            "max_soc_threshold": 90,
            "batteries_full": False,
        },
        power_allocation={"solar_for_car": 0},
        data=data,
    )

    assert decision["car_grid_charging"] is True
    reason = decision["car_grid_charging_reason"]
    assert "[Permissive: +20%]" in reason


def test_permissive_mode_not_shown_when_inactive():
    """Reason string should not show permissive mode when inactive."""
    config = {
        "car_permissive_threshold_multiplier": 1.2,
    }
    engine = _create_engine(config)

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
        "car_permissive_mode_active": False,
    }

    decision = engine._decide_car_grid_charging(
        price_analysis,
        battery_analysis={
            "average_soc": 50,
            "min_soc": 50,
            "max_soc_threshold": 90,
            "batteries_full": False,
        },
        power_allocation={"solar_for_car": 0},
        data=data,
    )

    assert decision["car_grid_charging"] is True
    reason = decision["car_grid_charging_reason"]
    assert "[Permissive:" not in reason


def test_permissive_mode_reason_shows_base_threshold_window():
    """Reason when waiting should reference base and permissive thresholds clearly."""
    config = {
        "car_permissive_threshold_multiplier": 1.2,
    }
    engine = _create_engine(config)

    price_analysis = {
        "data_available": True,
        "current_price": 0.196,
        "price_threshold": 0.172,
        "is_low_price": False,
        "very_low_price": False,
    }

    decision = engine._decide_car_grid_charging(
        price_analysis,
        battery_analysis={
            "average_soc": 50,
            "min_soc": 50,
            "max_soc_threshold": 90,
            "batteries_full": False,
        },
        power_allocation={"solar_for_car": 0},
        data={
            "previous_car_charging": False,
            "has_min_charging_window": False,
            "car_permissive_mode_active": True,
        },
    )

    assert decision["car_grid_charging"] is False
    reason = decision["car_grid_charging_reason"]
    assert "Price above base threshold" in reason
    assert "0.196€/kWh > 0.172€/kWh" in reason
    assert "within permissive limit (0.206€/kWh)" in reason
    assert "[Permissive: +20%]" in reason


def test_permissive_mode_shown_for_very_low_price_start():
    """Permissive mode indicator should appear for very low price start decisions."""
    config = {"car_permissive_threshold_multiplier": 1.25}
    engine = _create_engine(config)

    price_analysis = {
        "data_available": True,
        "current_price": 0.08,
        "price_threshold": 0.10,
        "is_low_price": True,
        "very_low_price": True,
    }

    data = {
        "previous_car_charging": False,
        "has_min_charging_window": True,
        "car_permissive_mode_active": True,
    }

    decision = engine._decide_car_grid_charging(
        price_analysis,
        battery_analysis={
            "average_soc": 60,
            "min_soc": 55,
            "max_soc_threshold": 90,
            "batteries_full": False,
        },
        power_allocation={"solar_for_car": 0},
        data=data,
    )

    assert decision["car_grid_charging"] is True
    reason = decision["car_grid_charging_reason"]
    assert "Very low price" in reason
    assert "[Permissive: +25%]" in reason


def test_permissive_mode_indicator_for_very_low_price_wait():
    """Permissive mode indicator should appear when waiting due to short window."""
    config = {"car_permissive_threshold_multiplier": 1.25}
    engine = _create_engine(config)

    price_analysis = {
        "data_available": True,
        "current_price": 0.08,
        "price_threshold": 0.10,
        "is_low_price": True,
        "very_low_price": True,
    }

    decision = engine._decide_car_grid_charging(
        price_analysis,
        battery_analysis={
            "average_soc": 60,
            "min_soc": 55,
            "max_soc_threshold": 90,
            "batteries_full": False,
        },
        power_allocation={"solar_for_car": 0},
        data={
            "previous_car_charging": False,
            "has_min_charging_window": False,
            "car_permissive_mode_active": True,
        },
    )

    assert decision["car_grid_charging"] is False
    reason = decision["car_grid_charging_reason"]
    assert "waiting for longer window" in reason
    assert "[Permissive: +25%]" in reason


def test_permissive_mode_works_with_hysteresis():
    """Permissive mode should work correctly with threshold floor hysteresis."""
    config = {
        "car_permissive_threshold_multiplier": 1.2,
    }
    engine = _create_engine(config)

    # Start charging with locked threshold
    data = {
        "previous_car_charging": True,  # Already charging
        "has_min_charging_window": True,
        "car_permissive_mode_active": True,
        "car_charging_locked_threshold": 0.12,  # Locked at 0.12
    }

    price_analysis = {
        "data_available": True,
        "current_price": 0.14,  # Above locked threshold but below permissive
        "price_threshold": 0.10,  # Current threshold dropped
        "is_low_price": False,
        "very_low_price": False,
    }

    # With hysteresis: max(0.12, 0.10) = 0.12
    # With permissive: 0.12 * 1.2 = 0.144
    # Price of 0.14 should still allow charging (below 0.144)

    decision = engine._decide_car_grid_charging(
        price_analysis,
        battery_analysis={
            "average_soc": 50,
            "min_soc": 50,
            "max_soc_threshold": 90,
            "batteries_full": False,
        },
        power_allocation={"solar_for_car": 0},
        data=data,
    )

    assert decision["car_grid_charging"] is True
    reason = decision["car_grid_charging_reason"]
    assert "[Permissive: +20%]" in reason
    assert "continuing" in reason


def test_permissive_mode_continues_charging_when_threshold_drops():
    """Permissive mode should allow continuing when base threshold drops below current price."""
    config = {
        "car_permissive_threshold_multiplier": 1.25,  # 25% increase
    }
    engine = _create_engine(config)

    # Scenario: Car started charging at 0.12, locked at 0.15 threshold.
    # Base threshold then dropped to 0.10 (dynamic threshold adjustment).
    # Current price 0.13 is above both base (0.10) and locked (0.12) thresholds.
    # Without permissive: would stop (0.13 > max(0.10, 0.12) = 0.12)
    # With permissive: continues (0.13 < max(0.10, 0.12) * 1.25 = 0.15)

    price_analysis = {
        "data_available": True,
        "current_price": 0.13,
        "price_threshold": 0.10,  # Base threshold dropped
        "is_low_price": False,
        "very_low_price": False,
    }

    data = {
        "previous_car_charging": True,
        "has_min_charging_window": True,
        "car_permissive_mode_active": True,
        "car_charging_locked_threshold": 0.12,  # Originally locked
    }

    decision = engine._decide_car_grid_charging(
        price_analysis,
        battery_analysis={
            "average_soc": 50,
            "min_soc": 50,
            "max_soc_threshold": 90,
            "batteries_full": False,
        },
        power_allocation={"solar_for_car": 0},
        data=data,
    )

    assert decision["car_grid_charging"] is True
    reason = decision["car_grid_charging_reason"]
    assert "[Permissive: +25%]" in reason
    assert "continuing" in reason


def test_permissive_mode_with_solar_allocation():
    """Permissive mode should work correctly with solar allocation."""
    config = {
        "car_permissive_threshold_multiplier": 1.3,
    }
    engine = _create_engine(config)

    price_analysis = {
        "data_available": True,
        "current_price": 0.17,
        "price_threshold": 0.15,
        "is_low_price": False,
        "very_low_price": False,
    }

    # With permissive: max(0.15, 0.15) * 1.3 = 0.195, so 0.17 allows continuing
    data = {
        "previous_car_charging": True,  # Already charging
        "has_min_charging_window": True,
        "car_permissive_mode_active": True,
        "car_charging_locked_threshold": 0.15,
    }

    decision = engine._decide_car_grid_charging(
        price_analysis,
        battery_analysis={
            "average_soc": 80,
            "min_soc": 80,
            "max_soc_threshold": 90,
            "batteries_full": False,
        },
        power_allocation={"solar_for_car": 1500},  # Solar available
        data=data,
    )

    assert decision["car_grid_charging"] is True
    reason = decision["car_grid_charging_reason"]
    assert "[Permissive: +30%]" in reason
    assert "solar" in reason.lower()
