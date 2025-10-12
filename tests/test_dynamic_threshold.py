"""Tests for the dynamic threshold analyser."""
import pytest

from custom_components.electricity_planner.dynamic_threshold import DynamicThresholdAnalyzer


def test_price_above_threshold_short_circuits():
    analyzer = DynamicThresholdAnalyzer(threshold=0.20, base_confidence=0.6)
    result = analyzer.analyze_price_window(
        current_price=0.25,
        highest_today=0.30,
        lowest_today=0.10,
        next_price=0.20,
    )
    assert result["should_charge"] is False
    assert result["confidence"] == 0.0
    assert "exceeds maximum threshold" in result["reason"]


def test_high_volatility_requires_bottom_range():
    analyzer = DynamicThresholdAnalyzer(threshold=0.20, base_confidence=0.6)
    result = analyzer.analyze_price_window(
        current_price=0.11,
        highest_today=0.30,
        lowest_today=0.05,
        next_price=0.12,
    )
    assert result["price_volatility"] > 0.5
    assert result["dynamic_threshold"] == pytest.approx(0.05 + (0.15 * 0.4))
    assert result["should_charge"] is True
    assert "Acceptable price" in result["reason"] or "Good price" in result["reason"]


def test_next_hour_better_reduces_confidence():
    analyzer = DynamicThresholdAnalyzer(threshold=0.18, base_confidence=0.6)
    result = analyzer.analyze_price_window(
        current_price=0.15,
        highest_today=0.20,
        lowest_today=0.08,
        next_price=0.12,
    )
    assert result["next_hour_better"] is True
    assert result["confidence"] < 0.6
    assert result["should_charge"] is False
    assert "better price" in result["reason"]


def test_low_volatility_accepts_broader_range():
    analyzer = DynamicThresholdAnalyzer(threshold=0.16, base_confidence=0.5)
    result = analyzer.analyze_price_window(
        current_price=0.14,
        highest_today=0.14,
        lowest_today=0.11,
        next_price=0.14,
    )
    assert result["price_volatility"] < 0.3
    assert result["dynamic_threshold"] == pytest.approx(0.11 + (0.05 * 0.8))
    assert result["should_charge"] is True
    assert result["confidence"] >= 0.5
