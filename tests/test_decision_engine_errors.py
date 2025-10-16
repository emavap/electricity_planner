"""Tests for error handling in the decision engine."""
import pytest

from custom_components.electricity_planner.decision_engine import (
    ChargingDecisionEngine,
)


@pytest.mark.asyncio
async def test_evaluate_charging_decision_no_price_data(mocker):
    """Test that evaluate_charging_decision handles no price data."""
    engine = ChargingDecisionEngine(hass=mocker.MagicMock(), config={})
    decision_data = {"battery_grid_charging_reason": "No decision made"}
    result = engine._create_no_data_decision(decision_data)
    assert (
        result["battery_grid_charging_reason"]
        == "No current price data available - all grid charging disabled for safety"
    )


@pytest.mark.asyncio
async def test_evaluate_charging_decision_no_battery_soc(mocker):
    """Test that evaluate_charging_decision handles no battery SOC."""
    engine = ChargingDecisionEngine(hass=mocker.MagicMock(), config={})
    result = engine._analyze_battery_status([])
    assert result["batteries_available"] is False