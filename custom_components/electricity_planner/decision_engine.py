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
        """Evaluate whether to charge batteries and car based on current conditions."""
        decision_data = {
            "battery_charging_recommended": False,
            "car_charging_recommended": False,
            "battery_charging_reason": "No decision made",
            "car_charging_reason": "No decision made",
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
            decision_data["battery_charging_reason"] = "No electricity price data available"
            decision_data["car_charging_reason"] = "No electricity price data available"
            return decision_data

        price_analysis = self._analyze_price(current_price)
        decision_data["price_analysis"] = price_analysis

        battery_analysis = self._analyze_battery_status(battery_soc_data)
        decision_data["battery_analysis"] = battery_analysis

        solar_analysis = self._analyze_solar_conditions(solar_forecast, solar_production)
        decision_data["solar_analysis"] = solar_analysis

        battery_decision = self._decide_battery_charging(
            price_analysis, battery_analysis, solar_analysis
        )
        decision_data.update(battery_decision)

        car_decision = self._decide_car_charging(
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
            "needs_charging": min_soc < min_soc_threshold,
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

    def _decide_battery_charging(
        self,
        price_analysis: dict[str, Any],
        battery_analysis: dict[str, Any],
        solar_analysis: dict[str, Any],
    ) -> dict[str, Any]:
        """Decide whether to charge batteries."""
        if battery_analysis["batteries_count"] == 0:
            return {
                "battery_charging_recommended": False,
                "battery_charging_reason": "No battery entities configured",
            }

        if battery_analysis["batteries_full"]:
            return {
                "battery_charging_recommended": False,
                "battery_charging_reason": f"Batteries above {battery_analysis['max_soc_threshold']}% SOC",
            }

        is_emergency = battery_analysis["needs_charging"]
        is_low_price = price_analysis["is_low_price"]
        has_good_solar_forecast = solar_analysis["has_good_forecast"]
        is_currently_producing = solar_analysis["is_producing"]

        if is_emergency:
            return {
                "battery_charging_recommended": True,
                "battery_charging_reason": f"Emergency charging - battery below {battery_analysis['min_soc_threshold']}%",
            }

        if is_currently_producing and not is_low_price:
            return {
                "battery_charging_recommended": False,
                "battery_charging_reason": "Solar producing and price not low - using solar power",
            }

        if is_low_price and not has_good_solar_forecast:
            return {
                "battery_charging_recommended": True,
                "battery_charging_reason": f"Low price ({price_analysis['current_price']:.3f}€/kWh) and poor solar forecast",
            }

        if is_low_price and has_good_solar_forecast:
            needed_charge = battery_analysis["max_soc_threshold"] - battery_analysis["average_soc"]
            if needed_charge > 30:  # If we need more than 30% charge
                return {
                    "battery_charging_recommended": True,
                    "battery_charging_reason": f"Low price and significant charge needed ({needed_charge:.1f}%)",
                }

        return {
            "battery_charging_recommended": False,
            "battery_charging_reason": f"Waiting - price: {price_analysis['current_price']:.3f}€/kWh, solar forecast available",
        }

    def _decide_car_charging(
        self,
        price_analysis: dict[str, Any],
        battery_analysis: dict[str, Any],
        solar_analysis: dict[str, Any],
    ) -> dict[str, Any]:
        """Decide whether to charge car."""
        is_low_price = price_analysis["is_low_price"]
        batteries_ok = not battery_analysis["needs_charging"] if battery_analysis["batteries_count"] > 0 else True
        is_night_time = datetime.now().hour >= 22 or datetime.now().hour <= 6

        if not batteries_ok:
            return {
                "car_charging_recommended": False,
                "car_charging_reason": "Batteries need charging first",
            }

        if is_low_price and is_night_time:
            return {
                "car_charging_recommended": True,
                "car_charging_reason": f"Low price ({price_analysis['current_price']:.3f}€/kWh) during night hours",
            }

        if solar_analysis["is_producing"] and batteries_ok:
            return {
                "car_charging_recommended": True,
                "car_charging_reason": "Solar producing and batteries OK",
            }

        return {
            "car_charging_recommended": False,
            "car_charging_reason": f"Waiting for better conditions - price: {price_analysis['current_price']:.3f}€/kWh",
        }