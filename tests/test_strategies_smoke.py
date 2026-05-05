"""Smoke tests for charging strategies - basic sanity checks."""
import pytest
from custom_components.electricity_planner.strategies import (
    SolarPriorityStrategy,
    VeryLowPriceStrategy,
    DynamicPriceStrategy,
    PredictiveChargingStrategy,
    SOCBasedChargingStrategy,
    SOCBufferChargingStrategy,
    StrategyManager,
)
from custom_components.electricity_planner.const import DEFAULT_MAX_SOC
from custom_components.electricity_planner.defaults import calculate_soc_price_multiplier


# NOTE: EmergencyChargingStrategy was removed - emergency logic is now handled
# by the PriceThresholdGuard in StrategyManager.evaluate(). The tests below
# verify the guard's emergency override behavior.


def test_emergency_guard_triggers_when_soc_low():
    """Test emergency guard activates when SOC is critically low, even at high price."""
    manager = StrategyManager(use_dynamic_threshold=True)
    context = {
        "battery_analysis": {"average_soc": 10},
        "config": {"emergency_soc_threshold": 15},
        "price_analysis": {
            "current_price": 0.5,  # Very high price - normally blocked
            "price_threshold": 0.15,
        },
    }

    should_charge, reason = manager.evaluate(context)
    assert should_charge is True
    assert "Emergency charge" in reason
    assert "10%" in reason


def test_emergency_guard_skips_when_soc_ok():
    """Test emergency guard doesn't activate when SOC is sufficient (price check applies)."""
    manager = StrategyManager(use_dynamic_threshold=True)
    context = {
        "battery_analysis": {"average_soc": 50},
        "config": {"emergency_soc_threshold": 15},
        "price_analysis": {
            "current_price": 0.5,  # Very high price - should be blocked
            "price_threshold": 0.15,
        },
    }

    should_charge, reason = manager.evaluate(context)
    assert should_charge is False
    assert "exceeds" in reason


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


def test_solar_priority_uses_default_max_soc_when_missing():
    """Missing max_soc_threshold should use the integration default, not a stale literal."""
    strategy = SolarPriorityStrategy()
    context = {
        "power_allocation": {"solar_for_batteries": 0, "remaining_solar": 500},
        "battery_analysis": {"average_soc": DEFAULT_MAX_SOC - 5},
    }

    should_charge, reason = strategy.should_charge(context)
    assert should_charge is False
    assert "preventing solar waste" in reason


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
    # Updated to match new user-friendly messages
    assert "waiting for solar" in reason_excellent.lower() or "surplus:" in reason_excellent.lower()
    assert "accepting okay prices" in reason_poor.lower() or "price conditions not optimal" in reason_poor.lower()
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


def test_strategy_manager_low_price_branches_use_stable_threshold_snapshot():
    """Traditional low-price strategies should use the same stable threshold as the guard."""
    manager = StrategyManager(use_dynamic_threshold=False)
    price_analysis = {
        "current_price": 0.13,
        "price_threshold": 0.10,
        "is_low_price": False,
        "very_low_price": False,
    }
    context = {
        "price_analysis": price_analysis,
        "battery_analysis": {"average_soc": 45, "max_soc_threshold": 90},
        "power_analysis": {"solar_surplus": 0, "significant_solar_surplus": False},
        "power_allocation": {"solar_for_batteries": 0, "remaining_solar": 0},
        "config": {"emergency_soc_threshold": 15},
        "battery_stable_threshold": 0.15,
    }

    should_charge, reason = manager.evaluate(context)

    assert should_charge is True
    assert "Medium SOC" in reason
    assert price_analysis["is_low_price"] is False


def test_strategy_manager_hard_blocks_grid_when_battery_solar_is_allocated():
    """Allocated battery solar should stop later strategies from re-enabling grid charging."""
    manager = StrategyManager(use_dynamic_threshold=True)
    context = {
        "price_analysis": {
            "current_price": 0.05,
            "price_threshold": 0.15,
            "is_low_price": True,
            "very_low_price": True,
        },
        "battery_analysis": {"average_soc": 25, "max_soc_threshold": 90},
        "power_analysis": {"solar_surplus": 2500, "significant_solar_surplus": True},
        "power_allocation": {"solar_for_batteries": 2000, "remaining_solar": 500},
        "config": {"emergency_soc_threshold": 15},
    }

    should_charge, reason = manager.evaluate(context)

    assert should_charge is False
    assert "solar power" in reason.lower()


# --- SOC Price Multiplier Tests ---


@pytest.mark.parametrize("soc,expected_multiplier", [
    (15.0, 1.3),   # At emergency threshold → max multiplier
    (10.0, 1.3),   # Below emergency → max multiplier (capped)
    (50.0, 1.0),   # At buffer target → no relaxation
    (70.0, 1.0),   # Above buffer target → no relaxation
    (32.5, 1.15),  # Midpoint → 50% of extra multiplier
])
def test_soc_price_multiplier_calculation(soc, expected_multiplier):
    """Test SOC price multiplier calculation with various SOC levels."""
    multiplier = calculate_soc_price_multiplier(
        current_soc=soc,
        emergency_soc=15.0,
        buffer_target_soc=50.0,
        max_multiplier=1.3,
    )
    assert abs(multiplier - expected_multiplier) < 0.01, (
        f"At SOC {soc}%, expected multiplier {expected_multiplier}, got {multiplier}"
    )


def test_soc_price_multiplier_invalid_max():
    """Test SOC multiplier clamps invalid max_multiplier."""
    # max_multiplier < 1.0 should be clamped to 1.0
    multiplier = calculate_soc_price_multiplier(
        current_soc=20.0,
        emergency_soc=15.0,
        buffer_target_soc=50.0,
        max_multiplier=0.8,  # Invalid - less than 1.0
    )
    assert multiplier == 1.0


def test_soc_price_multiplier_invalid_range():
    """Test SOC multiplier handles invalid buffer/emergency range."""
    # When buffer_target <= emergency, the soc_range check returns 1.0
    # But only if we get past the emergency check first
    # If current_soc is above the (invalid) buffer_target, it returns 1.0
    multiplier = calculate_soc_price_multiplier(
        current_soc=60.0,  # Above buffer_target
        emergency_soc=50.0,
        buffer_target_soc=30.0,  # Invalid - less than emergency
        max_multiplier=1.3,
    )
    # current_soc >= buffer_target_soc (60 >= 30), so returns 1.0
    assert multiplier == 1.0

    # When current_soc is between buffer and emergency (in invalid config)
    # The soc_range <= 0 check catches this
    multiplier2 = calculate_soc_price_multiplier(
        current_soc=40.0,  # Between buffer (30) and emergency (50)
        emergency_soc=50.0,
        buffer_target_soc=30.0,  # Invalid - less than emergency
        max_multiplier=1.3,
    )
    # 40 >= 30 (buffer_target), so returns 1.0
    assert multiplier2 == 1.0


def test_soc_buffer_strategy_triggers_when_threshold_relaxed():
    """Test SOC buffer strategy triggers when threshold is relaxed."""
    strategy = SOCBufferChargingStrategy()
    context = {
        "threshold_relaxed": True,
        "effective_threshold": 0.195,
        "soc_price_multiplier": 1.3,
        "price_analysis": {
            "current_price": 0.18,
            "price_threshold": 0.15,
        },
        "battery_analysis": {
            "average_soc": 20,
            "max_soc_threshold": 90,
        },
    }

    should_charge, reason = strategy.should_charge(context)
    assert should_charge is True
    assert "Buffer charging" in reason
    assert "SOC 20%" in reason
    assert "×1.30 multiplier" in reason


def test_soc_buffer_strategy_skips_when_price_below_base():
    """Test SOC buffer strategy skips when price is below base threshold."""
    strategy = SOCBufferChargingStrategy()
    context = {
        "threshold_relaxed": True,  # Relaxation active
        "effective_threshold": 0.195,
        "soc_price_multiplier": 1.3,
        "price_analysis": {
            "current_price": 0.10,  # Below base threshold - normal strategies should handle
            "price_threshold": 0.15,
        },
        "battery_analysis": {
            "average_soc": 20,
            "max_soc_threshold": 90,
        },
    }

    should_charge, reason = strategy.should_charge(context)
    # Should skip because price is below base threshold
    assert should_charge is False


def test_soc_buffer_strategy_skips_when_not_relaxed():
    """Test SOC buffer strategy skips when threshold is not relaxed."""
    strategy = SOCBufferChargingStrategy()
    context = {
        "threshold_relaxed": False,  # No relaxation
        "price_analysis": {
            "current_price": 0.10,
            "price_threshold": 0.15,
        },
        "battery_analysis": {
            "average_soc": 50,
            "max_soc_threshold": 90,
        },
    }

    should_charge, reason = strategy.should_charge(context)
    assert should_charge is False


def test_soc_buffer_strategy_skips_when_battery_full():
    """Test SOC buffer strategy skips when battery is already full."""
    strategy = SOCBufferChargingStrategy()
    context = {
        "threshold_relaxed": True,
        "effective_threshold": 0.195,
        "soc_price_multiplier": 1.3,
        "price_analysis": {
            "current_price": 0.18,
            "price_threshold": 0.15,
        },
        "battery_analysis": {
            "average_soc": 90,  # At max
            "max_soc_threshold": 90,
        },
    }

    should_charge, reason = strategy.should_charge(context)
    assert should_charge is False


def test_strategy_manager_applies_soc_price_multiplier():
    """Test strategy manager applies SOC-based price relaxation."""
    manager = StrategyManager(use_dynamic_threshold=True)

    # Price that would normally be blocked (0.18 > 0.15 threshold)
    # But with low SOC (20%), multiplier = 1.3, effective threshold = 0.195
    context = {
        "price_analysis": {
            "current_price": 0.18,
            "price_threshold": 0.15,
        },
        "battery_analysis": {
            "average_soc": 20,  # Low SOC
            "max_soc_threshold": 90,
        },
        "power_analysis": {
            "solar_surplus": 0,
            "significant_solar_surplus": False,
        },
        "config": {
            "emergency_soc_threshold": 15,
            "soc_price_multiplier_max": 1.3,
            "soc_buffer_target": 50,
        },
    }

    should_charge, reason = manager.evaluate(context)
    # Should charge because 0.18 < 0.195 (effective threshold with multiplier)
    assert should_charge is True, f"Expected charging due to SOC relaxation, got: {reason}"
    assert "Buffer charging" in reason or "multiplier" in reason.lower()


def test_strategy_manager_blocks_when_above_relaxed_threshold():
    """Test strategy manager blocks when price exceeds even the relaxed threshold."""
    manager = StrategyManager(use_dynamic_threshold=True)

    # Price too high even with relaxation (0.25 > 0.195 effective threshold)
    context = {
        "price_analysis": {
            "current_price": 0.25,
            "price_threshold": 0.15,
        },
        "battery_analysis": {
            "average_soc": 20,  # Low SOC, but price still too high
            "max_soc_threshold": 90,
        },
        "power_analysis": {
            "solar_surplus": 0,
            "significant_solar_surplus": False,
        },
        "config": {
            "emergency_soc_threshold": 15,
            "soc_price_multiplier_max": 1.3,
            "soc_buffer_target": 50,
        },
    }

    should_charge, reason = manager.evaluate(context)
    assert should_charge is False
    assert "exceeds maximum threshold" in reason
    # Should show the relaxed threshold in the reason
    assert "SOC multiplier" in reason or "×1." in reason


def test_strategy_manager_no_relaxation_at_high_soc():
    """Test strategy manager doesn't apply relaxation when SOC is high."""
    manager = StrategyManager(use_dynamic_threshold=True)

    # Same price that was OK at 20% SOC, but now at 60% SOC
    context = {
        "price_analysis": {
            "current_price": 0.18,
            "price_threshold": 0.15,
        },
        "battery_analysis": {
            "average_soc": 60,  # High enough SOC - no relaxation
            "max_soc_threshold": 90,
        },
        "power_analysis": {
            "solar_surplus": 0,
            "significant_solar_surplus": False,
        },
        "config": {
            "emergency_soc_threshold": 15,
            "soc_price_multiplier_max": 1.3,
            "soc_buffer_target": 50,
        },
    }

    should_charge, reason = manager.evaluate(context)
    assert should_charge is False
    assert "exceeds maximum threshold" in reason


# ---------------------------------------------------------------------------
# StrategyManager.evaluate() - fallback/advisory branches
# ---------------------------------------------------------------------------


def test_solar_priority_downgrade_at_emergency_soc_allows_safety_net_to_charge():
    """At emergency SOC, SolarPriority's "use solar" block is downgraded to advisory
    so SOCBasedChargingStrategy can still force grid charging."""
    # Dynamic pricing disabled so DynamicPriceStrategy doesn't interfere.
    manager = StrategyManager(use_dynamic_threshold=False)
    context = {
        "battery_analysis": {"average_soc": 10, "max_soc_threshold": 90},
        "price_analysis": {
            "current_price": 0.05,
            "price_threshold": 0.15,
            "highest_price": 0.3,
            "lowest_price": 0.05,
            "is_low_price": True,
        },
        "power_allocation": {"solar_for_batteries": 2000, "remaining_solar": 0},
        "power_analysis": {"significant_solar_surplus": False, "solar_surplus": 0},
        "config": {"emergency_soc_threshold": 15},
    }

    should_charge, reason = manager.evaluate(context)
    assert should_charge is True
    assert "Low SOC" in reason

    trace = manager.get_last_trace()
    strategies_seen = [e["strategy"] for e in trace]
    assert "SolarPriorityStrategy" in strategies_seen  # downgrade path exercised
    assert "SOCBasedChargingStrategy" in strategies_seen  # safety net continued
    # SolarPriority entry was False (downgraded), final safety-net entry was True
    solar_entry = next(e for e in trace if e["strategy"] == "SolarPriorityStrategy")
    socbased_entry = next(e for e in trace if e["strategy"] == "SOCBasedChargingStrategy")
    assert solar_entry["should_charge"] is False
    assert socbased_entry["should_charge"] is True


def test_evaluate_returns_last_advisory_reason_when_no_strategy_charges():
    """When only advisory False reasons are produced, last reason is returned."""
    manager = StrategyManager(use_dynamic_threshold=False)
    context = {
        "battery_analysis": {"average_soc": 55, "max_soc_threshold": 90},
        "price_analysis": {
            "current_price": 0.10,
            "price_threshold": 0.15,
            "highest_price": 0.3,
            "lowest_price": 0.05,
            "is_low_price": True,
            "significant_price_drop": True,
            "next_price": 0.05,
        },
        "power_allocation": {"solar_for_batteries": 0, "remaining_solar": 0},
        "power_analysis": {"significant_solar_surplus": False, "solar_surplus": 0},
        "config": {"emergency_soc_threshold": 15},
    }

    should_charge, reason = manager.evaluate(context)
    assert should_charge is False
    assert "waiting for significant price drop" in reason

    trace = manager.get_last_trace()
    # The final entry should be the AdvisoryReason marker appended by evaluate().
    assert trace[-1]["strategy"] == "AdvisoryReason"
    assert trace[-1]["should_charge"] is False
    assert trace[-1]["reason"] == reason


def test_evaluate_default_decision_when_no_strategy_votes():
    """All strategies silent (no reasons) → default 'price not favorable' reason."""
    manager = StrategyManager(use_dynamic_threshold=False)
    context = {
        "battery_analysis": {"average_soc": 55, "max_soc_threshold": 90},
        "price_analysis": {
            "current_price": 0.10,
            "price_threshold": 0.15,
            "highest_price": 0.3,
            "lowest_price": 0.05,
            "price_position": 0.5,
            "is_low_price": False,
        },
        "power_allocation": {"solar_for_batteries": 0, "remaining_solar": 0},
        "power_analysis": {"significant_solar_surplus": False, "solar_surplus": 0},
        "config": {"emergency_soc_threshold": 15},
    }

    should_charge, reason = manager.evaluate(context)
    assert should_charge is False
    assert "Price not favorable" in reason
    assert "0.100€/kWh" in reason
    assert "50% of daily range" in reason
    assert "SOC 55%" in reason

    trace = manager.get_last_trace()
    assert trace[-1]["strategy"] == "DefaultDecision"
    assert trace[-1]["priority"] == 999


def test_get_last_trace_returns_a_copy_not_internal_list():
    """Callers must not be able to mutate internal trace via get_last_trace()."""
    manager = StrategyManager(use_dynamic_threshold=False)
    manager.evaluate({
        "battery_analysis": {"average_soc": 55, "max_soc_threshold": 90},
        "price_analysis": {
            "current_price": 0.10,
            "price_threshold": 0.15,
            "is_low_price": False,
        },
        "power_allocation": {},
        "power_analysis": {},
        "config": {"emergency_soc_threshold": 15},
    })

    trace = manager.get_last_trace()
    original_len = len(trace)
    trace.append({"strategy": "Hacked"})
    assert len(manager.get_last_trace()) == original_len


# ---------------------------------------------------------------------------
# StrategyManager.get_dynamic_threshold() - None paths
# ---------------------------------------------------------------------------


def test_get_dynamic_threshold_returns_none_when_dynamic_pricing_disabled():
    """use_dynamic_threshold=False → dynamic_price_strategy is None → returns None."""
    manager = StrategyManager(use_dynamic_threshold=False)
    assert manager.dynamic_price_strategy is None
    assert manager.get_dynamic_threshold({"price_analysis": {"current_price": 0.1}}) is None


def test_get_dynamic_threshold_returns_none_when_analyzer_not_initialized():
    """Before first evaluate() call, the DynamicPriceStrategy has no analyzer."""
    manager = StrategyManager(use_dynamic_threshold=True)
    assert manager.dynamic_price_strategy is not None
    assert manager.dynamic_price_strategy.dynamic_analyzer is None
    context = {
        "price_analysis": {
            "current_price": 0.10,
            "highest_price": 0.30,
            "lowest_price": 0.05,
        }
    }
    assert manager.get_dynamic_threshold(context) is None


def test_get_dynamic_threshold_returns_none_when_prices_missing():
    """After analyzer is initialized, missing current/highest/lowest → None."""
    manager = StrategyManager(use_dynamic_threshold=True)
    # First run evaluate() to initialize the analyzer.
    manager.evaluate({
        "battery_analysis": {"average_soc": 50, "max_soc_threshold": 90},
        "price_analysis": {
            "current_price": 0.10,
            "price_threshold": 0.15,
            "highest_price": 0.3,
            "lowest_price": 0.05,
        },
        "power_allocation": {},
        "power_analysis": {},
        "config": {"emergency_soc_threshold": 15},
    })
    assert manager.dynamic_price_strategy.dynamic_analyzer is not None

    # Missing current_price
    assert manager.get_dynamic_threshold({"price_analysis": {"highest_price": 0.3, "lowest_price": 0.05}}) is None
    # Missing highest_price
    assert manager.get_dynamic_threshold({"price_analysis": {"current_price": 0.1, "lowest_price": 0.05}}) is None
    # Missing lowest_price
    assert manager.get_dynamic_threshold({"price_analysis": {"current_price": 0.1, "highest_price": 0.3}}) is None




if __name__ == "__main__":
    # Run smoke tests
    test_emergency_guard_triggers_when_soc_low()
    test_emergency_guard_skips_when_soc_ok()
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
