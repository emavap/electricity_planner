"""Unit tests for the decision engine."""
import pytest
from unittest.mock import Mock, MagicMock
from datetime import datetime, timedelta
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from custom_components.electricity_planner.decision_engine import ChargingDecisionEngine
from custom_components.electricity_planner.const import (
    CONF_PRICE_THRESHOLD,
    CONF_EMERGENCY_SOC_THRESHOLD,
    CONF_MAX_BATTERY_POWER,
    CONF_MAX_CAR_POWER,
)


class TestChargingDecisionEngine:
    """Test the charging decision engine."""
    
    @pytest.fixture
    def hass(self):
        """Create mock Home Assistant instance."""
        return Mock()
    
    @pytest.fixture
    def config(self):
        """Create test configuration."""
        return {
            CONF_PRICE_THRESHOLD: 0.15,
            CONF_EMERGENCY_SOC_THRESHOLD: 15,
            CONF_MAX_BATTERY_POWER: 3000,
            CONF_MAX_CAR_POWER: 11000,
        }
    
    @pytest.fixture
    def engine(self, hass, config):
        """Create decision engine instance."""
        return ChargingDecisionEngine(hass, config)
    
    @pytest.mark.asyncio
    async def test_emergency_charging_triggers(self, engine):
        """Test that emergency charging triggers when SOC is critically low."""
        data = {
            "current_price": 0.25,  # High price
            "highest_price": 0.30,
            "lowest_price": 0.10,
            "next_price": 0.20,
            "battery_soc": [
                {"entity_id": "sensor.battery1", "soc": 10},  # Below emergency
            ],
            "solar_production": 0,
            "house_consumption": 1000,
            "solar_surplus": 0,
        }
        
        result = await engine.evaluate_charging_decision(data)
        
        assert result["battery_grid_charging"] is True
        assert "Emergency" in result["battery_grid_charging_reason"]
        assert result["price_analysis"]["current_price"] == 0.25
    
    @pytest.mark.asyncio
    async def test_solar_priority_over_grid(self, engine):
        """Test that solar charging is preferred over grid when available."""
        data = {
            "current_price": 0.10,  # Low price
            "highest_price": 0.30,
            "lowest_price": 0.05,
            "next_price": 0.12,
            "battery_soc": [
                {"entity_id": "sensor.battery1", "soc": 50},
            ],
            "solar_production": 3000,
            "house_consumption": 1000,
            "solar_surplus": 2000,  # Significant surplus
        }
        
        result = await engine.evaluate_charging_decision(data)
        
        # Should prefer solar over grid even at low price
        assert result["power_allocation"]["solar_for_batteries"] > 0
        assert "solar" in result["battery_grid_charging_reason"].lower()
    
    @pytest.mark.asyncio
    async def test_very_low_price_charging(self, engine):
        """Test charging decision at very low prices."""
        data = {
            "current_price": 0.05,  # Very low (bottom of range)
            "highest_price": 0.30,
            "lowest_price": 0.05,
            "next_price": 0.06,
            "battery_soc": [
                {"entity_id": "sensor.battery1", "soc": 60},
            ],
            "solar_production": 0,
            "house_consumption": 1000,
            "solar_surplus": 0,
        }
        
        result = await engine.evaluate_charging_decision(data)
        
        assert result["battery_grid_charging"] is True
        assert result["price_analysis"]["very_low_price"] is True
        assert "very low price" in result["battery_grid_charging_reason"].lower()
    
    @pytest.mark.asyncio
    async def test_predictive_charging_wait(self, engine):
        """Test that charging waits when significant price drop is expected."""
        data = {
            "current_price": 0.14,  # Low but not very low
            "highest_price": 0.30,
            "lowest_price": 0.05,
            "next_price": 0.08,  # Significant drop coming
            "battery_soc": [
                {"entity_id": "sensor.battery1", "soc": 45},  # Above predictive minimum
            ],
            "solar_production": 0,
            "house_consumption": 1000,
            "solar_surplus": 0,
        }
        
        result = await engine.evaluate_charging_decision(data)
        
        # Should wait for better price
        assert result["battery_grid_charging"] is False
        assert result["price_analysis"]["significant_price_drop"] is True
        assert "waiting" in result["battery_grid_charging_reason"].lower()
    
    @pytest.mark.asyncio
    async def test_car_charging_restriction(self, engine):
        """Test car charger limit when grid charging not allowed."""
        data = {
            "current_price": 0.20,  # High price
            "highest_price": 0.30,
            "lowest_price": 0.10,
            "next_price": 0.18,
            "battery_soc": [
                {"entity_id": "sensor.battery1", "soc": 70},
            ],
            "solar_production": 1000,
            "house_consumption": 1000,
            "solar_surplus": 0,
            "car_charging_power": 5000,  # Car is charging
        }
        
        result = await engine.evaluate_charging_decision(data)
        
        assert result["car_grid_charging"] is False  # Not allowed at high price
        assert result["charger_limit"] == 1400  # Limited to 1.4kW
        assert "not allowed" in result["charger_limit_reason"].lower()
    
    @pytest.mark.asyncio
    async def test_battery_capacity_weighting(self, engine):
        """Test capacity-weighted SOC calculation."""
        # Add battery capacities to config
        engine.config[CONF_BATTERY_CAPACITIES] = {
            "sensor.battery1": 5.0,  # 5 kWh
            "sensor.battery2": 10.0,  # 10 kWh
        }
        
        data = {
            "current_price": 0.10,
            "highest_price": 0.30,
            "lowest_price": 0.05,
            "next_price": 0.12,
            "battery_soc": [
                {"entity_id": "sensor.battery1", "soc": 80},  # 4 kWh stored
                {"entity_id": "sensor.battery2", "soc": 50},  # 5 kWh stored
            ],
            "solar_production": 0,
            "house_consumption": 1000,
            "solar_surplus": 0,
        }
        
        result = await engine.evaluate_charging_decision(data)
        
        # Weighted average: (4 + 5) / 15 = 60%
        assert result["battery_analysis"]["average_soc"] == 60.0
        assert result["battery_analysis"]["capacity_weighted"] is True
    
    @pytest.mark.asyncio
    async def test_solar_allocation_priority(self, engine):
        """Test solar power allocation priorities."""
        data = {
            "current_price": 0.10,
            "highest_price": 0.30,
            "lowest_price": 0.05,
            "next_price": 0.12,
            "battery_soc": [
                {"entity_id": "sensor.battery1", "soc": 50},
            ],
            "solar_production": 5000,
            "house_consumption": 1000,
            "solar_surplus": 4000,
            "car_charging_power": 2000,  # Car using some power
        }
        
        result = await engine.evaluate_charging_decision(data)
        allocation = result["power_allocation"]
        
        # Car should get current usage tracked
        assert allocation["car_current_solar_usage"] > 0
        # Batteries should get allocation (not full)
        assert allocation["solar_for_batteries"] > 0
        # Total shouldn't exceed surplus
        assert allocation["total_allocated"] <= 4000
    
    @pytest.mark.asyncio
    async def test_no_price_data_safety(self, engine):
        """Test safe behavior when price data is unavailable."""
        data = {
            "current_price": None,  # No price data
            "battery_soc": [
                {"entity_id": "sensor.battery1", "soc": 30},
            ],
            "solar_production": 0,
            "house_consumption": 1000,
            "solar_surplus": 0,
        }
        
        result = await engine.evaluate_charging_decision(data)
        
        # Should disable all grid charging for safety
        assert result["battery_grid_charging"] is False
        assert result["car_grid_charging"] is False
        assert result["grid_setpoint"] == 0
        assert "safety" in result["battery_grid_charging_reason"].lower()
    
    @pytest.mark.asyncio
    async def test_grid_setpoint_calculation(self, engine):
        """Test grid setpoint calculation with various scenarios."""
        data = {
            "current_price": 0.10,
            "highest_price": 0.30,
            "lowest_price": 0.05,
            "next_price": 0.12,
            "battery_soc": [
                {"entity_id": "sensor.battery1", "soc": 40},
            ],
            "solar_production": 0,
            "house_consumption": 1000,
            "solar_surplus": 0,
            "car_charging_power": 7000,
            "monthly_grid_peak": 5000,
        }
        
        result = await engine.evaluate_charging_decision(data)
        
        # Should calculate appropriate grid setpoint
        assert result["grid_setpoint"] > 0
        assert "grid setpoint" in result["grid_setpoint_reason"].lower()
        # Should respect monthly peak limit
        assert result["grid_setpoint"] <= 5000 * 0.9  # 90% of peak


class TestPriceAnalysis:
    """Test price analysis functionality."""
    
    def test_price_position_calculation(self):
        """Test price position calculation."""
        from custom_components.electricity_planner.helpers import PriceCalculator
        
        calc = PriceCalculator()
        
        # Test normal range
        position = calc.calculate_price_position(0.15, 0.30, 0.10)
        assert position == 0.25  # (0.15 - 0.10) / (0.30 - 0.10) = 0.25
        
        # Test at lowest
        position = calc.calculate_price_position(0.10, 0.30, 0.10)
        assert position == 0.0
        
        # Test at highest
        position = calc.calculate_price_position(0.30, 0.30, 0.10)
        assert position == 1.0
        
        # Test invalid range (same high and low)
        position = calc.calculate_price_position(0.15, 0.15, 0.15)
        assert position == 0.5  # Should return neutral
    
    def test_significant_price_drop_detection(self):
        """Test significant price drop detection."""
        from custom_components.electricity_planner.helpers import PriceCalculator
        
        calc = PriceCalculator()
        
        # Test significant drop
        assert calc.is_significant_price_drop(0.20, 0.15, 0.15) is True  # 25% drop
        
        # Test insignificant drop
        assert calc.is_significant_price_drop(0.20, 0.18, 0.15) is False  # 10% drop
        
        # Test no next price
        assert calc.is_significant_price_drop(0.20, None, 0.15) is False
        
        # Test price increase
        assert calc.is_significant_price_drop(0.15, 0.20, 0.15) is False


class TestDataValidation:
    """Test data validation utilities."""
    
    def test_power_value_validation(self):
        """Test power value validation and clamping."""
        from custom_components.electricity_planner.helpers import DataValidator
        
        validator = DataValidator()
        
        # Test normal value
        assert validator.validate_power_value(1000) == 1000
        
        # Test below minimum
        assert validator.validate_power_value(-100) == 0
        
        # Test above maximum
        assert validator.validate_power_value(60000, max_value=50000) == 50000
        
        # Test with custom minimum
        assert validator.validate_power_value(50, min_value=100) == 100
    
    def test_battery_data_validation(self):
        """Test battery data validation."""
        from custom_components.electricity_planner.helpers import DataValidator
        
        validator = DataValidator()
        
        # Test valid data
        valid_data = [
            {"entity_id": "sensor.battery1", "soc": 50},
            {"entity_id": "sensor.battery2", "soc": 60},
        ]
        is_valid, msg = validator.validate_battery_data(valid_data)
        assert is_valid is True
        assert "2/2" in msg
        
        # Test with some invalid
        mixed_data = [
            {"entity_id": "sensor.battery1", "soc": 50},
            {"entity_id": "sensor.battery2", "soc": None},
            {"entity_id": "sensor.battery3", "soc": 150},  # Invalid range
        ]
        is_valid, msg = validator.validate_battery_data(mixed_data)
        assert is_valid is True  # At least one valid
        assert "1/3" in msg
        
        # Test all invalid
        invalid_data = [
            {"entity_id": "sensor.battery1", "soc": None},
            {"entity_id": "sensor.battery2", "soc": -10},
        ]
        is_valid, msg = validator.validate_battery_data(invalid_data)
        assert is_valid is False
        
        # Test empty
        is_valid, msg = validator.validate_battery_data([])
        assert is_valid is False
        assert "No battery" in msg


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
