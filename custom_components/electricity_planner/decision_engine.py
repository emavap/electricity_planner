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
        """Evaluate whether to charge batteries and car from grid based on current conditions."""
        decision_data = {
            "battery_grid_charging": False,
            "car_grid_charging": False,
            "battery_grid_charging_reason": "No decision made",
            "car_grid_charging_reason": "No decision made",
            "next_evaluation": datetime.now() + timedelta(minutes=5),
            "price_analysis": {},
            "solar_analysis": {},
            "battery_analysis": {},
        }

        current_price = data.get("electricity_price")
        battery_soc_data = data.get("battery_soc", [])
        solar_forecast = data.get("solar_forecast")
        solar_production = data.get("solar_production", 0)

        if not current_price:
            decision_data["battery_grid_charging_reason"] = "No electricity price data available"
            decision_data["car_grid_charging_reason"] = "No electricity price data available"
            return decision_data

        price_analysis = self._analyze_price(current_price)
        decision_data["price_analysis"] = price_analysis

        battery_analysis = self._analyze_battery_status(battery_soc_data)
        decision_data["battery_analysis"] = battery_analysis

        solar_analysis = self._analyze_solar_conditions(solar_forecast, solar_production)
        decision_data["solar_analysis"] = solar_analysis

        battery_decision = self._decide_battery_grid_charging(
            price_analysis, battery_analysis, solar_analysis
        )
        decision_data.update(battery_decision)

        car_decision = self._decide_car_grid_charging(
            price_analysis, battery_analysis, solar_analysis
        )
        decision_data.update(car_decision)

        return decision_data

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

    def _analyze_battery_status(self, battery_soc_data: list[dict[str, Any]]) -> dict[str, Any]:
        """Analyze battery status for all configured batteries."""
        if not battery_soc_data:
            return {
                "average_soc": None,
                "min_soc": None,
                "max_soc": None,
                "batteries_count": 0,
                "needs_charging": False,
                "batteries_full": False,
            }

        soc_values = [battery["soc"] for battery in battery_soc_data]
        min_soc_threshold = self.config.get(CONF_MIN_SOC_THRESHOLD, DEFAULT_MIN_SOC)
        max_soc_threshold = self.config.get(CONF_MAX_SOC_THRESHOLD, DEFAULT_MAX_SOC)

        average_soc = sum(soc_values) / len(soc_values)
        min_soc = min(soc_values)
        max_soc = max(soc_values)

        return {
            "average_soc": average_soc,
            "min_soc": min_soc,
            "max_soc": max_soc,
            "batteries_count": len(battery_soc_data),
            "batteries_full": min_soc >= max_soc_threshold,
            "min_soc_threshold": min_soc_threshold,
            "max_soc_threshold": max_soc_threshold,
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
        solar_analysis: dict[str, Any],
    ) -> dict[str, Any]:
        """Decide whether to charge batteries from grid based only on price and solar conditions."""
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

        is_low_price = price_analysis["is_low_price"]
        has_good_solar_forecast = solar_analysis["has_good_forecast"]
        is_currently_producing = solar_analysis["is_producing"]

        # If price is too high, never recommend grid charging
        if not is_low_price:
            return {
                "battery_grid_charging": False,
                "battery_grid_charging_reason": f"Price too high ({price_analysis['current_price']:.3f}€/kWh) - threshold: {price_analysis['price_threshold']:.3f}€/kWh",
            }

        # If solar is producing well, don't charge from grid even if price is low
        if is_currently_producing and solar_analysis["current_production"] > 1.0:  # >1kW production
            return {
                "battery_grid_charging": False,
                "battery_grid_charging_reason": f"Solar producing {solar_analysis['current_production']:.1f}kW - use solar instead of grid",
            }

        # Low price and poor/no solar forecast - charge from grid
        if is_low_price and not has_good_solar_forecast:
            return {
                "battery_grid_charging": True,
                "battery_grid_charging_reason": f"Low price ({price_analysis['current_price']:.3f}€/kWh) and poor solar forecast",
            }

        # Low price with good solar forecast - only charge if significant capacity needed
        if is_low_price and has_good_solar_forecast:
            needed_charge = battery_analysis["max_soc_threshold"] - battery_analysis["average_soc"]
            if needed_charge > 30:  # If we need more than 30% charge
                return {
                    "battery_grid_charging": True,
                    "battery_grid_charging_reason": f"Low price ({price_analysis['current_price']:.3f}€/kWh) and significant charge needed ({needed_charge:.1f}%)",
                }

        return {
            "battery_grid_charging": False,
            "battery_grid_charging_reason": f"Low price but good solar forecast expected - wait for solar",
        }

    def _decide_car_grid_charging(
        self,
        price_analysis: dict[str, Any],
        battery_analysis: dict[str, Any],
        solar_analysis: dict[str, Any],
    ) -> dict[str, Any]:
        """Decide whether to charge car from grid based only on price and solar conditions."""
        is_low_price = price_analysis["is_low_price"]
        is_night_time = datetime.now().hour >= 22 or datetime.now().hour <= 6
        is_currently_producing = solar_analysis["is_producing"]

        # If price is too high, never recommend grid charging
        if not is_low_price:
            return {
                "car_grid_charging": False,
                "car_grid_charging_reason": f"Price too high ({price_analysis['current_price']:.3f}€/kWh) - threshold: {price_analysis['price_threshold']:.3f}€/kWh",
            }

        # If solar is producing well, prefer solar over grid for car charging
        if is_currently_producing and solar_analysis["current_production"] > 2.0:  # >2kW for car
            return {
                "car_grid_charging": False,
                "car_grid_charging_reason": f"Solar producing {solar_analysis['current_production']:.1f}kW - use solar instead of grid",
            }

        # Low price during night hours - good time to charge from grid
        if is_low_price and is_night_time:
            return {
                "car_grid_charging": True,
                "car_grid_charging_reason": f"Low price ({price_analysis['current_price']:.3f}€/kWh) during night hours",
            }

        # Low price during day but poor solar - charge from grid
        if is_low_price and not solar_analysis["has_good_forecast"]:
            return {
                "car_grid_charging": True,
                "car_grid_charging_reason": f"Low price ({price_analysis['current_price']:.3f}€/kWh) and poor solar forecast",
            }

        return {
            "car_grid_charging": False,
            "car_grid_charging_reason": f"Low price but solar expected - wait for solar",
        }