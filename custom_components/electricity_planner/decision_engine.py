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
    DEFAULT_MIN_SOC,
    DEFAULT_MAX_SOC,
    DEFAULT_PRICE_THRESHOLD,
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
        decision_data: dict[str, Any] = {
            "battery_grid_charging": False,
            "car_grid_charging": False,
            "battery_grid_charging_reason": "No decision made",
            "car_grid_charging_reason": "No decision made",
            "charger_limit": 0,
            "grid_setpoint": 0,
            "charger_limit_reason": "No decision made",
            "grid_setpoint_reason": "No decision made",
            "next_evaluation": datetime.now() + timedelta(minutes=5),
            "price_analysis": {},
            "power_analysis": {},
            "battery_analysis": {},
            "solar_analysis": {},
        }

        current_price = data.get("current_price")
        if current_price is None:
            reason = "No current price data available - all grid charging disabled for safety"
            decision_data["battery_grid_charging_reason"] = reason
            decision_data["car_grid_charging_reason"] = reason
            decision_data["charger_limit_reason"] = "No price data - limiting to solar only"
            decision_data["grid_setpoint_reason"] = "No price data - grid setpoint set to 0"
            _LOGGER.warning("Critical price data unavailable - disabling all grid charging")
            return decision_data

        price_analysis = self._analyze_comprehensive_pricing(data)
        decision_data["price_analysis"] = price_analysis

        battery_analysis = self._analyze_battery_status(
            data.get("battery_soc", [])
        )
        decision_data["battery_analysis"] = battery_analysis

        power_analysis = self._analyze_power_flow(data)
        decision_data["power_analysis"] = power_analysis

        solar_analysis = self._analyze_solar_production(data)
        decision_data["solar_analysis"] = solar_analysis

        battery_decision = self._decide_battery_grid_charging(
            price_analysis, battery_analysis, power_analysis
        )
        decision_data.update(battery_decision)

        car_decision = self._decide_car_grid_charging(
            price_analysis, battery_analysis, power_analysis
        )
        decision_data.update(car_decision)

        charger_limit_decision = self._calculate_charger_limit(
            price_analysis, battery_analysis, power_analysis, data
        )
        decision_data.update(charger_limit_decision)

        grid_setpoint_decision = self._calculate_grid_setpoint(
            price_analysis, battery_analysis, power_analysis, data, 
            decision_data.get("charger_limit", 0)
        )
        decision_data.update(grid_setpoint_decision)

        return decision_data

    def _analyze_comprehensive_pricing(self, data: dict[str, Any]) -> dict[str, Any]:
        """Analyze comprehensive pricing data from Nord Pool."""
        current_price = data.get("current_price")
        highest_price = data.get("highest_price")
        lowest_price = data.get("lowest_price")
        next_price = data.get("next_price")

        price_threshold = self.config.get(CONF_PRICE_THRESHOLD, DEFAULT_PRICE_THRESHOLD)

        # Handle missing current price
        if current_price is None:
            return {
                "current_price": None,
                "highest_price": highest_price,
                "lowest_price": lowest_price,
                "next_price": next_price,
                "price_threshold": price_threshold,
                "is_low_price": False,
                "is_lowest_price": False,
                "price_position": None,
                "next_price_higher": False,
                "price_trend_improving": False,
                "very_low_price": False,
                "data_available": False,
            }

        # Calculate price position relative to daily range
        if (
            highest_price is not None
            and lowest_price is not None
            and highest_price > lowest_price
        ):
            price_position = (current_price - lowest_price) / (highest_price - lowest_price)
        else:
            price_position = 0.5  # Neutral if no valid range

        # Next price higher means worse for charging now; trend improving means next price is lower
        next_price_higher = next_price is not None and next_price > current_price
        price_trend_improving = next_price is not None and next_price < current_price

        return {
            "current_price": current_price,
            "highest_price": highest_price,
            "lowest_price": lowest_price,
            "next_price": next_price,
            "price_threshold": price_threshold,
            "is_low_price": current_price <= price_threshold,
            "is_lowest_price": lowest_price is not None and current_price == lowest_price,
            "price_position": price_position,  # 0=lowest, 1=highest
            "next_price_higher": next_price_higher,
            "price_trend_improving": price_trend_improving,
            "very_low_price": price_position <= 0.3,  # Bottom 30% of daily range
            "data_available": True,
        }

    def _analyze_power_flow(self, data: dict[str, Any]) -> dict[str, Any]:
        """Analyze current power flow and consumption."""
        solar_surplus = data.get("solar_surplus")
        car_charging_power = data.get("car_charging_power")
        
        # Handle unavailable sensors - use 0 as safe default for power calculations
        solar_surplus = solar_surplus if solar_surplus is not None else 0
        car_charging_power = car_charging_power if car_charging_power is not None else 0

        return {
            "solar_surplus": solar_surplus,
            "car_charging_power": car_charging_power,
            "has_solar_surplus": solar_surplus > 0,
            "significant_solar_surplus": solar_surplus > 1000,  # >1kW surplus
            "car_currently_charging": car_charging_power > 0,
            "available_surplus_for_batteries": max(0, solar_surplus - car_charging_power),
        }

    def _analyze_solar_production(self, data: dict[str, Any]) -> dict[str, Any]:
        """Analyze solar production status."""
        solar_surplus = data.get("solar_surplus")
        solar_surplus = solar_surplus if solar_surplus is not None else 0
        
        # Consider solar producing if there's any surplus
        is_producing = solar_surplus > 0
        
        return {
            "current_production": solar_surplus,
            "is_producing": is_producing,
            "forecast": None,  # Could be expanded later with forecast data
            "has_good_forecast": False,  # Could be expanded later
        }

    def _analyze_battery_status(self, battery_soc_data: list[dict[str, Any]]) -> dict[str, Any]:
        """Analyze battery status for all configured batteries."""
        if not battery_soc_data:
            return {
                "average_soc": None,
                "min_soc": None,
                "max_soc": None,
                "batteries_count": 0,
                "batteries_full": False,
            }

        # Only include batteries with valid SOC values (not unavailable/unknown)
        soc_values = [battery["soc"] for battery in battery_soc_data if "soc" in battery and battery["soc"] is not None]
        
        # If no valid SOC values are available, return safe defaults
        if not soc_values:
            _LOGGER.warning("All battery SOC sensors are unavailable - no charging decisions will be made")
            return {
                "average_soc": None,
                "min_soc": None,
                "max_soc": None,
                "batteries_count": len(battery_soc_data),
                "batteries_full": False,
                "batteries_available": False,
            }

        min_soc_threshold = self.config.get(CONF_MIN_SOC_THRESHOLD, DEFAULT_MIN_SOC)
        max_soc_threshold = self.config.get(CONF_MAX_SOC_THRESHOLD, DEFAULT_MAX_SOC)

        average_soc = sum(soc_values) / len(soc_values)
        min_soc = min(soc_values)
        max_soc = max(soc_values)

        return {
            "average_soc": average_soc,
            "min_soc": min_soc,
            "max_soc": max_soc,
            "batteries_count": len(soc_values),
            "batteries_full": min_soc >= max_soc_threshold,
            "min_soc_threshold": min_soc_threshold,
            "max_soc_threshold": max_soc_threshold,
            "remaining_capacity_percent": max_soc_threshold - average_soc,
            "batteries_available": True,
        }

    def _decide_battery_grid_charging(
        self,
        price_analysis: dict[str, Any],
        battery_analysis: dict[str, Any],
        power_analysis: dict[str, Any],
    ) -> dict[str, Any]:
        """Decide whether to charge batteries from grid based on comprehensive analysis."""
        if battery_analysis.get("batteries_count", 0) == 0:
            return {
                "battery_grid_charging": False,
                "battery_grid_charging_reason": "No battery entities configured",
            }
        
        # Check if battery data is available (not all sensors unavailable)
        if not battery_analysis.get("batteries_available", True):
            return {
                "battery_grid_charging": False,
                "battery_grid_charging_reason": "All battery SOC sensors unavailable - no charging decision possible",
            }

        if battery_analysis.get("batteries_full"):
            return {
                "battery_grid_charging": False,
                "battery_grid_charging_reason": f"Batteries above {battery_analysis.get('max_soc_threshold', '?')}% SOC",
            }

        if not price_analysis.get("data_available", True):
            return {
                "battery_grid_charging": False,
                "battery_grid_charging_reason": "No price data available",
            }

        if not price_analysis.get("is_low_price", False):
            current = price_analysis.get("current_price")
            threshold = price_analysis.get("price_threshold")
            return {
                "battery_grid_charging": False,
                "battery_grid_charging_reason": f"Price too high ({current:.3f}€/kWh) - threshold: {threshold:.3f}€/kWh",
            }

        # Enhanced logic for battery SOC and price trend analysis (BEFORE very low price logic)
        average_soc = battery_analysis.get("average_soc")
        current_price = price_analysis.get("current_price")
        next_price = price_analysis.get("next_price")
        
        # For batteries >= 50% SOC, be more selective about charging
        if average_soc is not None and average_soc >= 50:
            # Check if price is dropping significantly next hour (20%+ drop)
            if (next_price is not None and current_price is not None and 
                next_price < current_price * 0.8):  # Next price is 20%+ lower
                return {
                    "battery_grid_charging": False,
                    "battery_grid_charging_reason": f"Batteries at {average_soc:.0f}% - price dropping significantly next hour ({current_price:.3f}→{next_price:.3f}€/kWh)",
                }
            
            # For well-charged batteries (>= 60%) with solar production, avoid grid charging
            if power_analysis.get("has_solar_surplus") and average_soc >= 60:
                return {
                    "battery_grid_charging": False,
                    "battery_grid_charging_reason": f"Batteries well charged ({average_soc:.0f}%) with solar production ({power_analysis.get('solar_surplus', 0)}W) - use solar instead",
                }

        if power_analysis.get("significant_solar_surplus"):
            return {
                "battery_grid_charging": False,
                "battery_grid_charging_reason": f"Solar surplus available ({power_analysis.get('solar_surplus')}W) - use solar instead of grid",
            }

        if price_analysis.get("very_low_price"):
            current = price_analysis.get("current_price")
            return {
                "battery_grid_charging": True,
                "battery_grid_charging_reason": f"Very low price ({current:.3f}€/kWh) - bottom 30% of daily range",
            }
        
        # Original 30% threshold logic
        if average_soc is not None and average_soc >= 30 and not price_analysis.get("very_low_price"):
            return {
                "battery_grid_charging": False,
                "battery_grid_charging_reason": f"Batteries above 30% ({average_soc:.0f}%) and price not very low - no need to charge",
            }

        current = price_analysis.get("current_price")
        position = price_analysis.get("price_position", 0.5)
        return {
            "battery_grid_charging": False,
            "battery_grid_charging_reason": f"Price OK but not optimal - current: {current:.3f}€/kWh, position: {position:.0%}",
        }

    def _decide_car_grid_charging(
        self,
        price_analysis: dict[str, Any],
        battery_analysis: dict[str, Any],
        power_analysis: dict[str, Any],
    ) -> dict[str, Any]:
        """Decide whether to charge car from grid based on price analysis only."""
        if not price_analysis.get("data_available", True):
            return {
                "car_grid_charging": False,
                "car_grid_charging_reason": "No price data available",
            }

        if not price_analysis.get("is_low_price", False):
            current = price_analysis.get("current_price")
            threshold = price_analysis.get("price_threshold")
            return {
                "car_grid_charging": False,
                "car_grid_charging_reason": f"Price too high ({current:.3f}€/kWh) - threshold: {threshold:.3f}€/kWh",
            }

        if price_analysis.get("very_low_price"):
            current = price_analysis.get("current_price")
            return {
                "car_grid_charging": True,
                "car_grid_charging_reason": f"Very low price ({current:.3f}€/kWh) - bottom 30% of daily range",
            }

        if price_analysis.get("price_trend_improving"):
            next_price = price_analysis.get("next_price")
            return {
                "car_grid_charging": False,
                "car_grid_charging_reason": f"Price improving next hour ({next_price:.3f}€/kWh) - wait for better price",
            }

        if price_analysis.get("is_low_price"):
            current = price_analysis.get("current_price")
            return {
                "car_grid_charging": True,
                "car_grid_charging_reason": f"Low price ({current:.3f}€/kWh) - below threshold",
            }

        current = price_analysis.get("current_price")
        position = price_analysis.get("price_position", 0.5)
        return {
            "car_grid_charging": False,
            "car_grid_charging_reason": f"Price not favorable - current: {current:.3f}€/kWh, position: {position:.0%}",
        }

    def _calculate_charger_limit(
        self,
        price_analysis: dict[str, Any],
        battery_analysis: dict[str, Any],
        power_analysis: dict[str, Any],
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Calculate optimal charger power limit based on energy management scenario."""
        car_charging_power = power_analysis.get("car_charging_power", 0)
        
        if car_charging_power <= 0:
            return {
                "charger_limit": 0,
                "charger_limit_reason": "Car not currently charging",
            }

        solar_surplus = power_analysis.get("solar_surplus", 0)
        monthly_grid_peak = data.get("monthly_grid_peak", 0)
        max_grid_setpoint = max(monthly_grid_peak, 2500) if monthly_grid_peak and monthly_grid_peak > 2500 else 2500
        average_soc = battery_analysis.get("average_soc")
        
        # If battery data is unavailable, use conservative approach
        if average_soc is None:
            charger_limit = min(max_grid_setpoint, 11000)
            return {
                "charger_limit": int(charger_limit),
                "charger_limit_reason": f"Battery data unavailable - conservative limit ({int(charger_limit)}W)",
            }
        
        # If battery < 80%: Car gets grid setpoint only, surplus goes to batteries
        if average_soc < 80:
            charger_limit = min(max_grid_setpoint, 11000)
            return {
                "charger_limit": int(charger_limit),
                "charger_limit_reason": f"Battery {average_soc:.0f}% < 80% - car limited to grid setpoint ({int(charger_limit)}W), surplus for batteries",
            }
        
        # If battery ≥ 80%: Car can use surplus + grid setpoint
        available_power = solar_surplus + max_grid_setpoint
        charger_limit = min(available_power, 11000)
        return {
            "charger_limit": int(charger_limit),
            "charger_limit_reason": f"Battery {average_soc:.0f}% ≥ 80% - car can use surplus + grid ({int(charger_limit)}W = {solar_surplus}W + {max_grid_setpoint}W)",
        }

    def _calculate_grid_setpoint(
        self,
        price_analysis: dict[str, Any],
        battery_analysis: dict[str, Any],
        power_analysis: dict[str, Any],
        data: dict[str, Any],
        charger_limit: int,
    ) -> dict[str, Any]:
        """Calculate grid setpoint based on energy management scenario."""
        car_charging_power = power_analysis.get("car_charging_power", 0)
        solar_surplus = power_analysis.get("solar_surplus", 0)
        monthly_grid_peak = data.get("monthly_grid_peak", 0)
        battery_grid_charging = data.get("battery_grid_charging", False)
        average_soc = battery_analysis.get("average_soc")
        
        # If battery data is unavailable, use safe grid setpoint approach
        if average_soc is None:
            if significant_car_charging:
                # Only provide grid for car, no battery charging
                max_grid_setpoint = max(monthly_grid_peak, 2500) if monthly_grid_peak and monthly_grid_peak > 2500 else 2500
                grid_setpoint = min(car_charging_power, max_grid_setpoint)
                return {
                    "grid_setpoint": int(grid_setpoint),
                    "grid_setpoint_reason": f"Battery data unavailable - grid only for car ({int(grid_setpoint)}W)",
                }
            else:
                return {
                    "grid_setpoint": 0,
                    "grid_setpoint_reason": "Battery data unavailable and no car charging - grid setpoint 0W",
                }
        
        # Ignore car charging power below 100W (standby/measurement noise)
        significant_car_charging = car_charging_power >= 100
        
        # Determine maximum grid setpoint based on monthly peak
        max_grid_setpoint = max(monthly_grid_peak, 2500) if monthly_grid_peak and monthly_grid_peak > 2500 else 2500
        
        # Case 1: Car charging + battery < 80% - grid for car consumption, surplus for batteries
        if significant_car_charging and average_soc < 80:
            # Grid setpoint follows actual car consumption up to the charger limit and grid peak limit
            car_grid_need = min(car_charging_power, charger_limit, max_grid_setpoint)
            grid_setpoint = car_grid_need
            return {
                "grid_setpoint": int(grid_setpoint),
                "grid_setpoint_reason": f"Car drawing {car_charging_power}W, battery {average_soc:.0f}% < 80% - grid for car ({int(grid_setpoint)}W), surplus for batteries",
            }
        
        # Case 2: Car charging + battery ≥ 80% - grid supports car, can also charge batteries if decided
        if significant_car_charging and average_soc >= 80:
            # Grid covers car needs first
            car_grid_need = max(0, car_charging_power - solar_surplus)
            
            # If battery charging decision is on, add battery charging to grid setpoint
            if battery_grid_charging:
                # Grid for car + remaining capacity for batteries
                remaining_grid_capacity = max_grid_setpoint - car_grid_need
                battery_grid_power = max(0, remaining_grid_capacity)
                grid_setpoint = min(car_grid_need + battery_grid_power, max_grid_setpoint)
                return {
                    "grid_setpoint": int(grid_setpoint),
                    "grid_setpoint_reason": f"Car {car_charging_power}W + battery {average_soc:.0f}% charging - grid for car ({car_grid_need}W) + batteries ({battery_grid_power}W) = {int(grid_setpoint)}W",
                }
            else:
                grid_setpoint = min(car_grid_need, max_grid_setpoint)
                return {
                    "grid_setpoint": int(grid_setpoint),
                    "grid_setpoint_reason": f"Car drawing {car_charging_power}W, battery {average_soc:.0f}% ≥ 80% - grid covers car deficit ({int(grid_setpoint)}W = {car_charging_power}W - {solar_surplus}W surplus)",
                }
        
        # Case 3: No significant car charging, but battery charging decision is on
        if not significant_car_charging and battery_grid_charging:
            grid_setpoint = max_grid_setpoint
            return {
                "grid_setpoint": int(grid_setpoint),
                "grid_setpoint_reason": f"No car charging - grid setpoint for battery charging ({int(grid_setpoint)}W)",
            }
        
        # Case 4: No significant car charging, no battery charging
        return {
            "grid_setpoint": 0,
            "grid_setpoint_reason": "No significant car charging and no battery grid charging decision",
        }