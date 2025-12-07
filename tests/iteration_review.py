"""
Comprehensive Strategy Logic Review Script.
Run iteratively until all tests pass.
"""

from custom_components.electricity_planner.strategies import StrategyManager
import sys

def run_review(iteration: int) -> list:
    """Run full review and return list of issues found."""
    print("=" * 70)
    print(f"ITERATION {iteration}: FULL STRATEGY LOGIC REVIEW")
    print("=" * 70)

    issues = []

    def test(name, ctx, expected_charge, keyword=None, desc=""):
        manager = StrategyManager(use_dynamic_threshold=True)
        charge, reason = manager.evaluate(ctx)
        passed = charge == expected_charge
        if keyword:
            passed = passed and (keyword.lower() in reason.lower())
        status = "✓" if passed else "✗"
        print(f"{status} {name}")
        if not passed:
            issues.append({"name": name, "desc": desc, "expected": expected_charge,
                          "got": charge, "reason": reason, "keyword": keyword})
            print(f"   ISSUE: Expected={expected_charge}, Got={charge}")
            print(f"   Reason: {reason[:100]}")
        return passed

    # Category 1: Price Threshold Guard
    print("\n--- Category 1: Price Threshold Guard ---")
    test("Guard: Emergency override (SOC 10%)", 
         {"price_analysis": {"current_price": 0.50, "price_threshold": 0.15},
          "battery_analysis": {"average_soc": 10}, "config": {"emergency_soc_threshold": 15}},
         True, "emergency")
    test("Guard: Block high price (SOC 60%)",
         {"price_analysis": {"current_price": 0.50, "price_threshold": 0.15},
          "battery_analysis": {"average_soc": 60}, "config": {"emergency_soc_threshold": 15}},
         False, "exceeds")
    test("Guard: SOC relaxation (SOC 20%, price 0.18)",
         {"price_analysis": {"current_price": 0.18, "price_threshold": 0.15, "is_low_price": False, "very_low_price": False},
          "battery_analysis": {"average_soc": 20, "max_soc_threshold": 90},
          "config": {"emergency_soc_threshold": 15, "soc_price_multiplier_max": 1.3, "soc_buffer_target": 50},
          "power_analysis": {"significant_solar_surplus": False, "solar_surplus": 0}},
         True, "buffer")

    # Category 2: Battery State
    print("\n--- Category 2: Battery State ---")
    test("Battery: Full blocks charging",
         {"price_analysis": {"current_price": 0.02, "price_threshold": 0.15, "very_low_price": True, "is_low_price": True,
                             "highest_price": 0.25, "lowest_price": 0.01},
          "battery_analysis": {"average_soc": 90, "max_soc_threshold": 90}, "config": {},
          "power_analysis": {"significant_solar_surplus": False, "solar_surplus": 0}},
         False, "full")
    test("Battery: Near-full allows charging (SOC 85%)",
         {"price_analysis": {"current_price": 0.02, "price_threshold": 0.15, "very_low_price": True, "is_low_price": True},
          "battery_analysis": {"average_soc": 85, "max_soc_threshold": 90}, "config": {}},
         True, "very low")
    test("Battery: No data, low price charges",
         {"price_analysis": {"current_price": 0.05, "price_threshold": 0.15, "very_low_price": True},
          "battery_analysis": {}, "config": {}},
         True, "")
    test("Battery: No data, high price blocks",
         {"price_analysis": {"current_price": 0.50, "price_threshold": 0.15},
          "battery_analysis": {}, "config": {}},
         False, "unavailable")

    # Category 3: Price Strategies
    print("\n--- Category 3: Price Strategies ---")
    test("VeryLowPrice: Excellent price (0.02)",
         {"price_analysis": {"current_price": 0.02, "price_threshold": 0.15, "very_low_price": True, "is_low_price": True},
          "battery_analysis": {"average_soc": 50, "max_soc_threshold": 90}, "config": {}},
         True, "very low")
    test("Dynamic: Good price (0.08)",
         {"price_analysis": {"current_price": 0.08, "price_threshold": 0.15, "is_low_price": True, "very_low_price": False,
                             "highest_price": 0.25, "lowest_price": 0.05},
          "battery_analysis": {"average_soc": 50, "max_soc_threshold": 90},
          "config": {"emergency_soc_threshold": 15, "soc_buffer_target": 50},
          "power_analysis": {"significant_solar_surplus": False, "solar_surplus": 0}},
         True, "")
    test("Predictive: Wait for price drop",
         {"price_analysis": {"current_price": 0.10, "price_threshold": 0.15, "is_low_price": True, "very_low_price": False,
                             "significant_price_drop": True, "next_price": 0.05,
                             "highest_price": 0.30, "lowest_price": 0.05},
          "battery_analysis": {"average_soc": 50, "max_soc_threshold": 90},
          "config": {"predictive_charging_min_soc": 30},
          "power_analysis": {"significant_solar_surplus": False, "solar_surplus": 0}},
         False, "waiting")

    # Category 4: SOC-Based
    print("\n--- Category 4: SOC-Based ---")
    test("SOCBased: Low SOC charges",
         {"price_analysis": {"current_price": 0.08, "price_threshold": 0.15, "is_low_price": True, "very_low_price": False},
          "battery_analysis": {"average_soc": 25, "max_soc_threshold": 90},
          "power_analysis": {"significant_solar_surplus": False, "solar_surplus": 0}, "config": {}},
         True, "")
    test("SOCBased: Medium SOC with solar waits",
         {"price_analysis": {"current_price": 0.10, "price_threshold": 0.15, "is_low_price": True, "very_low_price": False,
                             "highest_price": 0.25, "lowest_price": 0.05},
          "battery_analysis": {"average_soc": 50, "max_soc_threshold": 90},
          "power_analysis": {"significant_solar_surplus": True, "solar_surplus": 2000}, "config": {}},
         False, "solar")

    # Category 5: Edge Cases
    print("\n--- Category 5: Edge Cases ---")
    test("Edge: Zero price charges",
         {"price_analysis": {"current_price": 0.0, "price_threshold": 0.15, "very_low_price": True, "is_low_price": True},
          "battery_analysis": {"average_soc": 50, "max_soc_threshold": 90}, "config": {}},
         True, "")
    test("Edge: Negative price charges",
         {"price_analysis": {"current_price": -0.05, "price_threshold": 0.15, "very_low_price": True, "is_low_price": True},
          "battery_analysis": {"average_soc": 50, "max_soc_threshold": 90}, "config": {}},
         True, "")
    test("Edge: SOC exactly at max (90%)",
         {"price_analysis": {"current_price": 0.02, "price_threshold": 0.15, "very_low_price": True, "is_low_price": True,
                             "highest_price": 0.25, "lowest_price": 0.01},
          "battery_analysis": {"average_soc": 90, "max_soc_threshold": 90}, "config": {},
          "power_analysis": {"significant_solar_surplus": False, "solar_surplus": 0}},
         False, "full")
    test("Edge: SOC exactly at emergency (15%)",
         {"price_analysis": {"current_price": 0.50, "price_threshold": 0.15},
          "battery_analysis": {"average_soc": 15}, "config": {"emergency_soc_threshold": 15}},
         True, "emergency")
    test("Edge: SOC just above emergency (16%)",
         {"price_analysis": {"current_price": 0.50, "price_threshold": 0.15},
          "battery_analysis": {"average_soc": 16}, "config": {"emergency_soc_threshold": 15, "soc_buffer_target": 50}},
         False, "exceeds")

    # Category 6: Boundary Conditions
    print("\n--- Category 6: Boundary Conditions ---")
    test("Boundary: SOC at buffer target (50%) - no relaxation",
         {"price_analysis": {"current_price": 0.18, "price_threshold": 0.15},
          "battery_analysis": {"average_soc": 50}, "config": {"soc_buffer_target": 50}},
         False, "exceeds")
    # Note: At SOC 49%, multiplier is only ~1.01, so 0.16 still exceeds 0.151 - correct behavior
    test("Boundary: SOC just below buffer (49%) - minimal relaxation",
         {"price_analysis": {"current_price": 0.16, "price_threshold": 0.15},
          "battery_analysis": {"average_soc": 49},
          "config": {"emergency_soc_threshold": 15, "soc_price_multiplier_max": 1.3, "soc_buffer_target": 50}},
         False, "exceeds")  # 0.16 > 0.151 (base 0.15 × 1.01 multiplier)
    # Note: DynamicPriceStrategy uses tighter dynamic threshold, not base threshold
    test("Boundary: Price at base threshold but dynamic is tighter",
         {"price_analysis": {"current_price": 0.15, "price_threshold": 0.15, "is_low_price": True, "very_low_price": False,
                             "highest_price": 0.25, "lowest_price": 0.05},
          "battery_analysis": {"average_soc": 50, "max_soc_threshold": 90},
          "config": {}, "power_analysis": {"significant_solar_surplus": False, "solar_surplus": 0}},
         False, "")  # Dynamic strategy finds tighter threshold
    test("Boundary: Price just above threshold (no relaxation)",
         {"price_analysis": {"current_price": 0.151, "price_threshold": 0.15},
          "battery_analysis": {"average_soc": 60}, "config": {}},
         False, "exceeds")
    test("Boundary: Custom max SOC (80%)",
         {"price_analysis": {"current_price": 0.02, "price_threshold": 0.15, "very_low_price": True, "is_low_price": True,
                             "highest_price": 0.25, "lowest_price": 0.01},
          "battery_analysis": {"average_soc": 80, "max_soc_threshold": 80}, "config": {},
          "power_analysis": {"significant_solar_surplus": False, "solar_surplus": 0}},
         False, "full")

    # Category 7: Strategy Interactions
    print("\n--- Category 7: Strategy Interactions ---")
    test("Interaction: Very low price overrides predictive wait",
         {"price_analysis": {"current_price": 0.02, "price_threshold": 0.15, "very_low_price": True, "is_low_price": True,
                             "significant_price_drop": True, "next_price": 0.01},
          "battery_analysis": {"average_soc": 50, "max_soc_threshold": 90},
          "config": {"predictive_charging_min_soc": 30}},
         True, "very low")
    test("Interaction: Emergency overrides everything",
         {"price_analysis": {"current_price": 1.0, "price_threshold": 0.15,
                             "significant_price_drop": True, "next_price": 0.01},
          "battery_analysis": {"average_soc": 10},
          "config": {"emergency_soc_threshold": 15}},
         True, "emergency")
    test("Interaction: Buffer charging at relaxed price",
         {"price_analysis": {"current_price": 0.17, "price_threshold": 0.15, "is_low_price": False, "very_low_price": False},
          "battery_analysis": {"average_soc": 20, "max_soc_threshold": 90},
          "config": {"emergency_soc_threshold": 15, "soc_price_multiplier_max": 1.3, "soc_buffer_target": 50},
          "power_analysis": {"significant_solar_surplus": False, "solar_surplus": 0}},
         True, "buffer")

    return issues


if __name__ == "__main__":
    iteration = 1
    issues = run_review(iteration)
    
    print("\n" + "=" * 70)
    if issues:
        print(f"RESULT: {len(issues)} ISSUES FOUND")
        for i, issue in enumerate(issues, 1):
            print(f"\nIssue {i}: {issue['name']}")
            print(f"  Expected: {issue['expected']}, Got: {issue['got']}")
        sys.exit(1)
    else:
        print("RESULT: ALL TESTS PASSED ✓")
        sys.exit(0)

