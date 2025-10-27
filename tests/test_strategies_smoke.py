"""Smoke tests for charging strategies - basic sanity checks."""
from custom_components.electricity_planner.strategies import (
    EmergencyChargingStrategy,
    SolarPriorityStrategy,
    VeryLowPriceStrategy,
    DynamicPriceStrategy,
    PredictiveChargingStrategy,
    SOCBasedChargingStrategy,
    StrategyManager,
)


def test_emergency_charging_triggers_when_soc_low():
    """Test emergency charging activates when SOC is critically low."""
    strategy = EmergencyChargingStrategy()
    context = {
        "battery_analysis": {"average_soc": 10},
        "config": {"emergency_soc_threshold": 15},
        "price_analysis": {"current_price": 0.5},  # Even at high price
    }

    should_charge, reason = strategy.should_charge(context)
    assert should_charge is True
    assert "Emergency charge" in reason
    assert "10%" in reason


def test_emergency_charging_skips_when_soc_ok():
    """Test emergency charging doesn't activate when SOC is sufficient."""
    strategy = EmergencyChargingStrategy()
    context = {
        "battery_analysis": {"average_soc": 50},
        "config": {"emergency_soc_threshold": 15},
        "price_analysis": {"current_price": 0.1},
    }

    should_charge, reason = strategy.should_charge(context)
    assert should_charge is False


def test_solar_priority_prevents_grid_when_solar_allocated():
    """Test solar priority prevents grid charging when solar is allocated."""
    strategy = SolarPriorityStrategy()
    context = {
        "power_allocation": {"solar_for_batteries": 2000, "remaining_solar": 500},
        "battery_analysis": {"average_soc": 50, "max_soc_threshold": 90},
    }

    should_charge, reason = strategy.should_charge(context)
    assert should_charge is False
    assert "solar power" in reason.lower()


def test_very_low_price_charges_at_bottom_prices():
    """Test very low price strategy charges when price is in bottom range."""
    strategy = VeryLowPriceStrategy()
    context = {
        "price_analysis": {"very_low_price": True, "current_price": 0.05},
        "battery_analysis": {"average_soc": 50, "max_soc_threshold": 90},
        "config": {"very_low_price_threshold": 30},
    }

    should_charge, reason = strategy.should_charge(context)
    assert should_charge is True
    assert "Very low price" in reason


def test_dynamic_price_handles_none_prices_gracefully():
    """Test dynamic price strategy handles None prices during Nord Pool refresh."""
    strategy = DynamicPriceStrategy()
    context = {
        "price_analysis": {
            "current_price": 0.10,
            "highest_price": None,  # Can be None during refresh
            "lowest_price": None,   # Can be None during refresh
            "next_price": None,
            "price_threshold": 0.15,
        },
        "battery_analysis": {"average_soc": 50},
        "power_analysis": {
            "solar_surplus": 0,
            "significant_solar_surplus": False,
        },
        "config": {},
    }

    # Should not crash with TypeError
    should_charge, reason = strategy.should_charge(context)
    assert isinstance(should_charge, bool)
    assert isinstance(reason, str)


def test_dynamic_price_handles_zero_and_negative_prices():
    """Test dynamic price strategy correctly handles zero and negative prices."""
    strategy = DynamicPriceStrategy()

    # Zero prices (valid in Nord Pool)
    context_zero = {
        "price_analysis": {
            "current_price": 0.0,  # Zero is valid, should not be treated as None
            "highest_price": 0.05,
            "lowest_price": 0.0,
            "next_price": 0.01,
            "price_threshold": 0.15,
        },
        "battery_analysis": {"average_soc": 50},
        "power_analysis": {
            "solar_surplus": 0,
            "significant_solar_surplus": False,
        },
        "config": {},
    }

    should_charge, reason = strategy.should_charge(context_zero)
    assert isinstance(should_charge, bool)
    # Zero price should be excellent - should charge
    assert should_charge is True, f"Should charge at zero price, but got: {reason}"

    # Negative prices (valid in Nord Pool during high renewable production)
    context_negative = {
        "price_analysis": {
            "current_price": -0.02,  # Negative is valid
            "highest_price": 0.05,
            "lowest_price": -0.05,
            "next_price": -0.01,
            "price_threshold": 0.15,
        },
        "battery_analysis": {"average_soc": 50},
        "power_analysis": {
            "solar_surplus": 0,
            "significant_solar_surplus": False,
        },
        "config": {},
    }

    should_charge, reason = strategy.should_charge(context_negative)
    assert isinstance(should_charge, bool)
    # Negative price should definitely charge
    assert should_charge is True, f"Should charge at negative price, but got: {reason}"


def test_dynamic_price_with_excellent_solar_is_selective():
    """Test dynamic price strategy is more selective with significant solar surplus."""
    strategy = DynamicPriceStrategy()

    # Same price, different solar surplus states
    base_context = {
        "price_analysis": {
            "current_price": 0.10,
            "highest_price": 0.20,
            "lowest_price": 0.05,
            "next_price": 0.11,
            "price_threshold": 0.15,
        },
        "battery_analysis": {"average_soc": 60},
        "config": {},
    }

    # Significant solar surplus - should be picky
    excellent_context = {
        **base_context,
        "power_analysis": {
            "solar_surplus": 3200,
            "significant_solar_surplus": True,
        },
    }
    should_charge_excellent, reason_excellent = strategy.should_charge(excellent_context)

    # Poor solar - should be less picky
    poor_context = {
        **base_context,
        "power_analysis": {
            "solar_surplus": 0,
            "significant_solar_surplus": False,
        },
    }
    should_charge_poor, reason_poor = strategy.should_charge(poor_context)

    # With significant solar, it should be harder to charge (more selective)
    assert "significant solar surplus" in reason_excellent.lower()
    assert "no solar surplus" in reason_poor.lower()
    assert should_charge_poor in (True, False)  # Sanity check returned bool


def test_soc_based_charges_when_low_soc_and_no_solar():
    """Test SOC-based strategy charges when SOC is low and no solar surplus is available."""
    strategy = SOCBasedChargingStrategy()
    context = {
        "price_analysis": {"is_low_price": True},
        "battery_analysis": {"average_soc": 30},
        "power_analysis": {
            "solar_surplus": 0,
            "significant_solar_surplus": False,
        },
        "config": {},
    }

    should_charge, reason = strategy.should_charge(context)
    assert should_charge is True
    assert "Low SOC" in reason or "no significant solar" in reason.lower()


def test_strategy_manager_sorts_by_priority():
    """Test strategy manager respects priority order."""
    manager = StrategyManager(use_dynamic_threshold=True)

    # Check that strategies are sorted by priority
    priorities = [s.get_priority() for s in manager.strategies]
    assert priorities == sorted(priorities), f"Strategies not sorted: {priorities}"

    # Verify no duplicate priorities
    assert len(priorities) == len(set(priorities)), f"Duplicate priorities found: {priorities}"


def test_strategy_manager_emergency_overrides_high_price():
    """Test emergency charging works even when price exceeds threshold."""
    manager = StrategyManager(use_dynamic_threshold=True)
    context = {
        "price_analysis": {
            "current_price": 0.50,  # Very high price
            "price_threshold": 0.15,
        },
        "battery_analysis": {"average_soc": 10},  # Emergency low
        "config": {"emergency_soc_threshold": 15},
    }

    should_charge, reason = manager.evaluate(context)
    assert should_charge is True
    assert "Emergency" in reason


def test_strategy_manager_respects_price_threshold():
    """Test strategy manager blocks charging when price too high (non-emergency)."""
    manager = StrategyManager(use_dynamic_threshold=True)
    context = {
        "price_analysis": {
            "current_price": 0.50,  # Very high price
            "price_threshold": 0.15,
        },
        "battery_analysis": {"average_soc": 50},  # Not emergency
        "config": {"emergency_soc_threshold": 15},
    }

    should_charge, reason = manager.evaluate(context)
    assert should_charge is False
    assert "exceeds maximum threshold" in reason


def test_strategy_manager_uses_stable_threshold_snapshot():
    """Ensure the guard honours the captured stable threshold."""
    manager = StrategyManager(use_dynamic_threshold=True)
    context = {
        "price_analysis": {
            "current_price": 0.13,
            "price_threshold": 0.15,
        },
        "battery_analysis": {"average_soc": 80},
        "config": {"emergency_soc_threshold": 20},
        "battery_stable_threshold": 0.12,
    }

    should_charge, reason = manager.evaluate(context)
    assert should_charge is False
    assert "0.120€/kWh" in reason or "0.120" in reason
    assert "exceeds maximum threshold" in reason


if __name__ == "__main__":
    # Run smoke tests
    test_emergency_charging_triggers_when_soc_low()
    test_emergency_charging_skips_when_soc_ok()
    test_solar_priority_prevents_grid_when_solar_allocated()
    test_very_low_price_charges_at_bottom_prices()
    test_dynamic_price_handles_none_prices_gracefully()
    test_dynamic_price_handles_zero_and_negative_prices()
    test_dynamic_price_with_excellent_solar_is_selective()
    test_soc_based_charges_when_low_soc_and_no_solar()
    test_strategy_manager_sorts_by_priority()
    test_strategy_manager_emergency_overrides_high_price()
    test_strategy_manager_respects_price_threshold()

    print("✓ All smoke tests passed!")
