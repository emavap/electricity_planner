"""Decision engine for electricity planning."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict

from homeassistant.core import HomeAssistant

from .const import (
    CONF_MIN_SOC_THRESHOLD,
    CONF_MAX_SOC_THRESHOLD,
    CONF_PRICE_THRESHOLD,
    CONF_SOLAR_FORECAST_HOURS,
    DEFAULT_MIN_SOC,
    DEFAULT_MAX_SOC,
    DEFAULT_PRICE_THRESHOLD,
    DEFAULT_SOLAR_FORECAST_HOURS,
)

_LOGGER = logging.getLogger(__name__)


class ChargingDecisionEngine:
    """Engine for making charging decisions based on multiple factors."""

    def __init__(self, hass: HomeAssistant, config: dict[str, Any]) -> None:
        """Initialize the decision engine."""
        self.hass = hass
        self.config = config

    async def evaluate_charging_decision(self, data: dict[str, Any]) -> dict[str, Any]:
        """Evaluate whether to charge batteries and car from grid based on comprehensive data."""
        decision_data = {
            "battery_grid_charging": False,
            "car_grid_charging": False,
            "battery_grid_charging_reason": "No decision made",
            "car_grid_charging_reason": "No decision made",
            "next_evaluation": datetime.now() + timedelta(minutes=5),
            "price_analysis": {},
            "power_analysis": {},
            "battery_analysis": {},
        }

        current_price = data.get("current_price")
        if not current_price:
            decision_data["battery_grid_charging_reason"] = "No current price data available"
            decision_data["car_grid_charging_reason"] = "No current price data available"
            return decision_data

        price_analysis = self._analyze_comprehensive_pricing(data)
        decision_data["price_analysis"] = price_analysis

        battery_analysis = self._analyze_battery_status(
            data.get("battery_soc", []), 
            data.get("battery_capacity", [])
        )
        decision_data["battery_analysis"] = battery_analysis

        power_analysis = self._analyze_power_flow(data)
        decision_data["power_analysis"] = power_analysis

        battery_decision = self._decide_battery_grid_charging(
            price_analysis, battery_analysis, power_analysis
        )
        decision_data.update(battery_decision)

        car_decision = self._decide_car_grid_charging(
            price_analysis, battery_analysis, power_analysis
        )
        decision_data.update(car_decision)

        return decision_data

    def _analyze_comprehensive_pricing(self, data: dict[str, Any]) -> dict[str, Any]:
        """Analyze comprehensive pricing data from Nord Pool."""
        current_price = data.get("current_price", 0)
        highest_price = data.get("highest_price", 0)
        lowest_price = data.get("lowest_price", 0)
        next_price = data.get("next_price", 0)
        
        price_threshold = self.config.get(CONF_PRICE_THRESHOLD, DEFAULT_PRICE_THRESHOLD)
        
        # Calculate price position relative to daily range
        if highest_price > lowest_price:
            price_position = (current_price - lowest_price) / (highest_price - lowest_price)
        else:
            price_position = 0.5  # Neutral if no price range
        
        return {
            "current_price": current_price,
            "highest_price": highest_price,
            "lowest_price": lowest_price,
            "next_price": next_price,
            "price_threshold": price_threshold,
            "is_low_price": current_price <= price_threshold,
            "is_lowest_price": current_price == lowest_price,
            "price_position": price_position,  # 0=lowest, 1=highest
            "next_price_higher": next_price > current_price,
            "price_trend_improving": next_price < current_price,
            "very_low_price": price_position < 0.3,  # Bottom 30% of daily range
        }

    def _analyze_power_flow(self, data: dict[str, Any]) -> dict[str, Any]:
        """Analyze current power flow and consumption."""
        house_consumption = data.get("house_consumption", 0)
        solar_surplus = data.get("solar_surplus", 0)
        car_charging_power = data.get("car_charging_power", 0)
        
        return {
            "house_consumption": house_consumption,
            "solar_surplus": solar_surplus,
            "car_charging_power": car_charging_power,
            "has_solar_surplus": solar_surplus > 0,
            "significant_solar_surplus": solar_surplus > 1000,  # >1kW surplus
            "car_currently_charging": car_charging_power > 0,
            "available_surplus_for_batteries": max(0, solar_surplus - car_charging_power),
        }

    def _analyze_price(self, current_price: float) -> dict[str, Any]:
        """Analyze current electricity price conditions."""
        price_threshold = self.config.get(CONF_PRICE_THRESHOLD, DEFAULT_PRICE_THRESHOLD)
        
        return {
            "current_price": current_price,
            "price_threshold": price_threshold,
            "is_low_price": current_price <= price_threshold,
            "price_ratio": current_price / price_threshold if price_threshold > 0 else 1.0,
            "recommendation": "charge" if current_price <= price_threshold else "wait",
        }

    def _analyze_battery_status(self, battery_soc_data: list[dict[str, Any]], battery_capacity_data: list[dict[str, Any]]) -> dict[str, Any]:
        """Analyze battery status for all configured batteries."""
        if not battery_soc_data:
            return {
                "average_soc": None,
                "min_soc": None,
                "max_soc": None,
                "batteries_count": 0,
                "total_capacity": 0,
                "batteries_full": False,
            }

        soc_values = [battery["soc"] for battery in battery_soc_data]
        min_soc_threshold = self.config.get(CONF_MIN_SOC_THRESHOLD, DEFAULT_MIN_SOC)
        max_soc_threshold = self.config.get(CONF_MAX_SOC_THRESHOLD, DEFAULT_MAX_SOC)

        average_soc = sum(soc_values) / len(soc_values)
        min_soc = min(soc_values)
        max_soc = max(soc_values)
        
        # Calculate total capacity if available
        total_capacity = 0
        if battery_capacity_data:
            capacity_values = [battery["capacity"] for battery in battery_capacity_data]
            total_capacity = sum(capacity_values)

        return {
            "average_soc": average_soc,
            "min_soc": min_soc,
            "max_soc": max_soc,
            "batteries_count": len(battery_soc_data),
            "total_capacity": total_capacity,
            "batteries_full": min_soc >= max_soc_threshold,
            "min_soc_threshold": min_soc_threshold,
            "max_soc_threshold": max_soc_threshold,
            "remaining_capacity_percent": max_soc_threshold - average_soc,
        }

    def _analyze_solar_conditions(
        self, solar_forecast: float | None, solar_production: float
    ) -> dict[str, Any]:
        """Analyze solar production and forecast conditions."""
        forecast_hours = self.config.get(CONF_SOLAR_FORECAST_HOURS, DEFAULT_SOLAR_FORECAST_HOURS)
        
        is_producing = solar_production > 0
        has_forecast = solar_forecast is not None and solar_forecast > 0
        
        expected_production = solar_forecast if has_forecast else 0
        
        return {
            "current_production": solar_production,
            "forecast": solar_forecast,
            "forecast_hours": forecast_hours,
            "is_producing": is_producing,
            "has_good_forecast": has_forecast and expected_production > 2.0,  # kW threshold
            "recommendation": "wait_for_solar" if has_forecast and expected_production > 1.0 else "charge_from_grid",
        }

    def _decide_battery_grid_charging(
        self,
        price_analysis: dict[str, Any],
        battery_analysis: dict[str, Any],
        power_analysis: dict[str, Any],
    ) -> dict[str, Any]:
        """Decide whether to charge batteries from grid based on comprehensive analysis."""
        if battery_analysis["batteries_count"] == 0:
            return {
                "battery_grid_charging": False,
                "battery_grid_charging_reason": "No battery entities configured",
            }

        if battery_analysis["batteries_full"]:
            return {
                "battery_grid_charging": False,
                "battery_grid_charging_reason": f"Batteries above {battery_analysis['max_soc_threshold']}% SOC",
            }

        # If price is too high, never recommend grid charging
        if not price_analysis["is_low_price"]:
            return {
                "battery_grid_charging": False,
                "battery_grid_charging_reason": f"Price too high ({price_analysis['current_price']:.3f}€/kWh) - threshold: {price_analysis['price_threshold']:.3f}€/kWh",
            }

        # If there's significant solar surplus, use that instead of grid
        if power_analysis["significant_solar_surplus"]:
            return {
                "battery_grid_charging": False,
                "battery_grid_charging_reason": f"Solar surplus available ({power_analysis['solar_surplus']}W) - use solar instead of grid",
            }

        # Very low price (bottom 30% of daily range) - charge from grid
        if price_analysis["very_low_price"]:
            return {
                "battery_grid_charging": True,
                "battery_grid_charging_reason": f"Very low price ({price_analysis['current_price']:.3f}€/kWh) - bottom 30% of daily range",
            }

        # Price improving next hour and we have capacity needs
        if price_analysis["price_trend_improving"] and battery_analysis["remaining_capacity_percent"] > 20:
            return {
                "battery_grid_charging": True,
                "battery_grid_charging_reason": f"Price improving next hour ({price_analysis['next_price']:.3f}€/kWh) and capacity needed",
            }

        return {
            "battery_grid_charging": False,
            "battery_grid_charging_reason": f"Price OK but not optimal - current: {price_analysis['current_price']:.3f}€/kWh, position: {price_analysis['price_position']:.0%}",
        }

    def _decide_car_grid_charging(
        self,
        price_analysis: dict[str, Any],
        battery_analysis: dict[str, Any],
        power_analysis: dict[str, Any],
    ) -> dict[str, Any]:
        """Decide whether to charge car from grid based on price analysis only."""
        # If price is too high, never recommend grid charging
        if not price_analysis["is_low_price"]:
            return {
                "car_grid_charging": False,
                "car_grid_charging_reason": f"Price too high ({price_analysis['current_price']:.3f}€/kWh) - threshold: {price_analysis['price_threshold']:.3f}€/kWh",
            }

        # Very low price (bottom 30% of daily range) - charge from grid
        if price_analysis["very_low_price"]:
            return {
                "car_grid_charging": True,
                "car_grid_charging_reason": f"Very low price ({price_analysis['current_price']:.3f}€/kWh) - bottom 30% of daily range",
            }

        # Price improving next hour - good time to charge now
        if price_analysis["price_trend_improving"]:
            return {
                "car_grid_charging": True,
                "car_grid_charging_reason": f"Price improving next hour ({price_analysis['next_price']:.3f}€/kWh) - charge now",
            }

        # Just low price (below threshold) - charge from grid
        if price_analysis["is_low_price"]:
            return {
                "car_grid_charging": True,
                "car_grid_charging_reason": f"Low price ({price_analysis['current_price']:.3f}€/kWh) - below threshold",
            }

        return {
            "car_grid_charging": False,
            "car_grid_charging_reason": f"Price not favorable - current: {price_analysis['current_price']:.3f}€/kWh, position: {price_analysis['price_position']:.0%}",
        }