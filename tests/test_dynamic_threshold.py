"""Test dynamic threshold behavior vs simple threshold."""
import pytest
from unittest.mock import Mock
from datetime import datetime

from custom_components.electricity_planner.dynamic_threshold import (
    DynamicThresholdAnalyzer,
    PriceRankingStrategy,
)


class TestDynamicThreshold:
    """Test dynamic threshold logic."""
    
    def test_simple_vs_dynamic_threshold(self):
        """Compare simple threshold vs dynamic threshold decisions."""
        # Setup
        max_threshold = 0.15  # Maximum acceptable price
        analyzer = DynamicThresholdAnalyzer(max_threshold)
        
        # Test scenarios
        scenarios = [
            # (current_price, highest, lowest, next_price, simple_decision, expected_dynamic)
            (0.14, 0.30, 0.05, 0.13, True, False),  # Just below threshold but not optimal
            (0.08, 0.30, 0.05, 0.06, True, True),   # Good price in range
            (0.06, 0.30, 0.05, 0.05, True, True),   # Very good price
            (0.12, 0.30, 0.05, 0.08, True, False),  # Below threshold but better price coming
            (0.07, 0.15, 0.05, 0.06, True, True),   # Good in narrow range
            (0.14, 0.15, 0.13, 0.14, True, True),   # Low volatility, most prices acceptable
            (0.16, 0.30, 0.05, 0.15, False, False), # Above threshold - both reject
        ]
        
        print("\n=== Simple Threshold vs Dynamic Threshold Comparison ===")
        print(f"Maximum threshold: {max_threshold:.3f} €/kWh\n")
        
        for current, highest, lowest, next_p, simple, expected in scenarios:
            # Simple threshold logic
            simple_decision = current <= max_threshold
            
            # Dynamic threshold logic
            analysis = analyzer.analyze_price_window(
                current_price=current,
                highest_today=highest,
                lowest_today=lowest,
                next_price=next_p
            )
            
            dynamic_decision = analysis["should_charge"]
            
            # Print comparison
            print(f"Price: {current:.3f} €/kWh (Range: {lowest:.3f}-{highest:.3f})")
            print(f"  Simple threshold: {'✓ CHARGE' if simple_decision else '✗ NO CHARGE'}")
            print(f"  Dynamic threshold: {'✓ CHARGE' if dynamic_decision else '✗ NO CHARGE'}")
            print(f"  Dynamic confidence: {analysis['confidence']:.1%}")
            print(f"  Dynamic reasoning: {analysis['reason']}")
            print(f"  Price quality: {analysis['price_quality']:.1%}")
            print(f"  Dynamic threshold: {analysis['dynamic_threshold']:.3f} €/kWh")
            print()
            
            assert simple_decision == simple
            assert dynamic_decision == expected
    
    def test_soc_influence_on_dynamic_threshold(self):
        """Test how battery SOC affects dynamic threshold decisions."""
        analyzer = DynamicThresholdAnalyzer(0.15)
        
        # Same price scenario, different SOC levels
        price_scenario = {
            "current_price": 0.10,
            "highest_today": 0.20,
            "lowest_today": 0.05,
            "next_price": 0.09
        }
        
        print("\n=== SOC Influence on Dynamic Threshold ===")
        print(f"Price scenario: {price_scenario['current_price']:.3f} €/kWh\n")
        
        # Test different SOC levels
        soc_levels = [20, 40, 60, 80, 95]
        confidence_thresholds = [0.4, 0.5, 0.6, 0.7, 0.8]
        
        for soc, conf_threshold in zip(soc_levels, confidence_thresholds):
            analysis = analyzer.analyze_price_window(**price_scenario)
            
            # Determine if would charge based on SOC-adjusted confidence
            would_charge = analysis["confidence"] >= conf_threshold
            
            print(f"SOC: {soc}% - Confidence needed: {conf_threshold:.1%}")
            print(f"  Analysis confidence: {analysis['confidence']:.1%}")
            print(f"  Decision: {'✓ CHARGE' if would_charge else '✗ WAIT'}")
            print(f"  Reasoning: More {'selective' if soc > 60 else 'aggressive'} due to SOC")
            print()
    
    def test_volatility_impact(self):
        """Test how price volatility affects dynamic threshold."""
        analyzer = DynamicThresholdAnalyzer(0.15)
        
        print("\n=== Price Volatility Impact ===")
        
        # High volatility scenario
        high_vol = analyzer.analyze_price_window(
            current_price=0.10,
            highest_today=0.30,
            lowest_today=0.03,
            next_price=0.09
        )
        
        # Low volatility scenario
        low_vol = analyzer.analyze_price_window(
            current_price=0.10,
            highest_today=0.12,
            lowest_today=0.08,
            next_price=0.09
        )
        
        print("High Volatility (0.03-0.30 €/kWh):")
        print(f"  Dynamic threshold: {high_vol['dynamic_threshold']:.3f} €/kWh")
        print(f"  Decision: {'✓ CHARGE' if high_vol['should_charge'] else '✗ NO CHARGE'}")
        print(f"  Reasoning: Be selective when prices vary widely")
        print()
        
        print("Low Volatility (0.08-0.12 €/kWh):")
        print(f"  Dynamic threshold: {low_vol['dynamic_threshold']:.3f} €/kWh")
        print(f"  Decision: {'✓ CHARGE' if low_vol['should_charge'] else '✗ NO CHARGE'}")
        print(f"  Reasoning: Less selective when prices are stable")
    
    def test_future_price_impact(self):
        """Test how future price predictions affect decisions."""
        analyzer = DynamicThresholdAnalyzer(0.15)
        
        print("\n=== Future Price Impact ===")
        
        # Price dropping soon
        dropping = analyzer.analyze_price_window(
            current_price=0.10,
            highest_today=0.20,
            lowest_today=0.05,
            next_price=0.06,
            next_6h_prices=[0.06, 0.05, 0.05, 0.07, 0.08, 0.09]
        )
        
        # Price rising soon
        rising = analyzer.analyze_price_window(
            current_price=0.10,
            highest_today=0.20,
            lowest_today=0.05,
            next_price=0.12,
            next_6h_prices=[0.12, 0.14, 0.15, 0.13, 0.11, 0.10]
        )
        
        print("Price Dropping (current 0.10, next 6h: 0.05-0.09):")
        print(f"  Decision: {'✓ CHARGE' if dropping['should_charge'] else '✗ WAIT'}")
        print(f"  Confidence: {dropping['confidence']:.1%}")
        print(f"  Reasoning: {dropping['reason']}")
        print()
        
        print("Price Rising (current 0.10, next 6h: 0.10-0.15):")
        print(f"  Decision: {'✓ CHARGE' if rising['should_charge'] else '✗ WAIT'}")
        print(f"  Confidence: {rising['confidence']:.1%}")
        print(f"  Reasoning: {rising['reason']}")


class TestPriceRanking:
    """Test price ranking strategy."""
    
    def test_ranking_within_window(self):
        """Test price ranking within time window."""
        strategy = PriceRankingStrategy(window_hours=24)
        
        # Create mock price history and forecast
        now = datetime.now()
        history = [
            (now.replace(hour=h), 0.05 + h * 0.01)
            for h in range(0, 12)
        ]
        forecast = [
            (now.replace(hour=h), 0.10 + h * 0.005)
            for h in range(13, 24)
        ]
        
        # Test current price ranking
        result = strategy.rank_current_price(
            current_price=0.08,
            price_history=history,
            price_forecast=forecast
        )
        
        print("\n=== Price Ranking Test ===")
        print(f"Current price: 0.08 €/kWh")
        print(f"Rank percentile: {result['rank_percentile']:.0f}%")
        print(f"Is good time: {result['is_good_time']}")
        print(f"Better prices: {result['better_count']}")
        print(f"Worse prices: {result['worse_count']}")
        print(f"Reasoning: {result['reason']}")
        
        assert result["rank_percentile"] < 40  # Should be in good range
        assert result["is_good_time"] is True


if __name__ == "__main__":
    # Run tests with visual output
    test_dynamic = TestDynamicThreshold()
    test_dynamic.test_simple_vs_dynamic_threshold()
    test_dynamic.test_soc_influence_on_dynamic_threshold()
    test_dynamic.test_volatility_impact()
    test_dynamic.test_future_price_impact()
    
    test_ranking = TestPriceRanking()
    test_ranking.test_ranking_within_window()
