"""Decision engine for electricity planning."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant

from .const import (
    CONF_MIN_SOC_THRESHOLD,
    CONF_MAX_SOC_THRESHOLD,
    CONF_PRICE_THRESHOLD,
    CONF_WEATHER_ENTITY,
    CONF_EMERGENCY_SOC_THRESHOLD,
    CONF_VERY_LOW_PRICE_THRESHOLD,
    CONF_SIGNIFICANT_SOLAR_THRESHOLD,
    CONF_POOR_SOLAR_FORECAST_THRESHOLD,
    CONF_EXCELLENT_SOLAR_FORECAST_THRESHOLD,
    CONF_FEEDIN_PRICE_THRESHOLD,
    DEFAULT_MIN_SOC,
    DEFAULT_MAX_SOC,
    DEFAULT_PRICE_THRESHOLD,
    DEFAULT_EMERGENCY_SOC,
    DEFAULT_VERY_LOW_PRICE_THRESHOLD,
    DEFAULT_SIGNIFICANT_SOLAR_THRESHOLD,
    DEFAULT_POOR_SOLAR_FORECAST,
    DEFAULT_EXCELLENT_SOLAR_FORECAST,
    DEFAULT_FEEDIN_PRICE_THRESHOLD,
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
            "feedin_solar": False,
            "feedin_solar_reason": "No decision made",
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
            decision_data["feedin_solar_reason"] = "No price data - feed-in decision disabled"
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

        solar_forecast = await self._analyze_solar_forecast(data)
        decision_data["solar_forecast"] = solar_forecast

        time_context = self._get_time_context()
        decision_data["time_context"] = time_context

        # Hierarchical power allocation to prevent solar surplus race conditions
        power_allocation = self._allocate_solar_power(
            power_analysis, battery_analysis, price_analysis, solar_forecast, time_context
        )
        decision_data["power_allocation"] = power_allocation

        battery_decision = self._decide_battery_grid_charging_enhanced(
            price_analysis, battery_analysis, power_allocation, solar_forecast, time_context
        )
        decision_data.update(battery_decision)

        car_decision = self._decide_car_grid_charging(
            price_analysis, battery_analysis, power_allocation
        )
        decision_data.update(car_decision)

        # Pass fresh decisions and power allocation to subsequent functions
        decision_data_for_downstream = {**data, **decision_data}
        
        charger_limit_decision = self._calculate_charger_limit(
            price_analysis, battery_analysis, power_allocation, decision_data_for_downstream
        )
        decision_data.update(charger_limit_decision)
        decision_data_for_downstream.update(charger_limit_decision)

        grid_setpoint_decision = self._calculate_grid_setpoint(
            price_analysis, battery_analysis, power_allocation, decision_data_for_downstream, 
            decision_data.get("charger_limit", 0)
        )
        decision_data.update(grid_setpoint_decision)

        # Feed-in solar decision (last, after all power is allocated)
        feedin_decision = self._decide_feedin_solar(
            price_analysis, power_allocation
        )
        decision_data.update(feedin_decision)

        return decision_data

    def _decide_feedin_solar(
        self,
        price_analysis: dict[str, Any],
        power_allocation: dict[str, Any],
    ) -> dict[str, Any]:
        """Decide whether to enable solar feed-in based on price threshold."""
        if not price_analysis.get("data_available", True):
            return {
                "feedin_solar": False,
                "feedin_solar_reason": "No price data available - disable feed-in for safety",
            }

        current_price = price_analysis.get("current_price", 0)
        feedin_threshold = self.config.get(CONF_FEEDIN_PRICE_THRESHOLD, DEFAULT_FEEDIN_PRICE_THRESHOLD)
        remaining_solar = power_allocation.get("remaining_solar", 0)
        
        # Only consider feed-in if there's remaining solar power to export
        if remaining_solar <= 0:
            return {
                "feedin_solar": False,  # No need for feed-in when no surplus
                "feedin_solar_reason": f"No solar surplus to export ({remaining_solar}W) - feed-in disabled",
            }
        
        # Main decision: Enable feed-in if price is above threshold
        if current_price >= feedin_threshold:
            return {
                "feedin_solar": True,
                "feedin_solar_reason": f"Price {current_price:.3f}€/kWh ≥ {feedin_threshold:.3f}€/kWh threshold - enable solar export ({remaining_solar}W available)",
            }
        else:
            return {
                "feedin_solar": False,
                "feedin_solar_reason": f"Price {current_price:.3f}€/kWh < {feedin_threshold:.3f}€/kWh threshold - disable solar export, keep {remaining_solar}W surplus local",
            }

    def _analyze_comprehensive_pricing(self, data: dict[str, Any]) -> dict[str, Any]:
        """Analyze comprehensive pricing data from Nord Pool."""
        current_price = data.get("current_price")
        highest_price = data.get("highest_price")
        lowest_price = data.get("lowest_price")
        next_price = data.get("next_price")

        price_threshold = self.config.get(CONF_PRICE_THRESHOLD, DEFAULT_PRICE_THRESHOLD)
        very_low_price_threshold = self.config.get(CONF_VERY_LOW_PRICE_THRESHOLD, DEFAULT_VERY_LOW_PRICE_THRESHOLD) / 100.0

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
        
        # Predictive logic: significant price improvement coming
        significant_price_drop = (next_price is not None and 
                                 current_price > 0 and 
                                 (current_price - next_price) / current_price > 0.15)  # 15% drop

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
            "significant_price_drop": significant_price_drop,
            "very_low_price": price_position <= very_low_price_threshold,  # Configurable threshold of daily range
            "data_available": True,
        }

    def _analyze_power_flow(self, data: dict[str, Any]) -> dict[str, Any]:
        """Analyze current power flow and consumption."""
        solar_surplus = data.get("solar_surplus")
        car_charging_power = data.get("car_charging_power")
        
        # Handle unavailable sensors - use 0 as safe default for power calculations
        solar_surplus = solar_surplus if solar_surplus is not None else 0
        car_charging_power = car_charging_power if car_charging_power is not None else 0
        
        # Get configurable solar threshold
        significant_solar_threshold = self.config.get(CONF_SIGNIFICANT_SOLAR_THRESHOLD, DEFAULT_SIGNIFICANT_SOLAR_THRESHOLD)

        return {
            "solar_surplus": solar_surplus,
            "car_charging_power": car_charging_power,
            "has_solar_surplus": solar_surplus > 0,
            "significant_solar_surplus": solar_surplus > significant_solar_threshold,
            "car_currently_charging": car_charging_power > 0,
            "available_surplus_for_batteries": max(0, solar_surplus - car_charging_power),
            "significant_solar_threshold": significant_solar_threshold,
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
        }

    def _allocate_solar_power(
        self,
        power_analysis: dict[str, Any],
        battery_analysis: dict[str, Any],
        price_analysis: dict[str, Any],
        solar_forecast: dict[str, Any],
        time_context: dict[str, Any],
    ) -> dict[str, Any]:
        """Hierarchically allocate solar surplus: batteries first, then cars."""
        solar_surplus = power_analysis.get("solar_surplus", 0)
        significant_solar_threshold = self.config.get(CONF_SIGNIFICANT_SOLAR_THRESHOLD, DEFAULT_SIGNIFICANT_SOLAR_THRESHOLD)
        
        if solar_surplus <= significant_solar_threshold:
            # Even with insufficient solar, account for current car consumption
            car_charging_power = power_analysis.get("car_charging_power", 0)
            if car_charging_power > 100:  # Car is actually charging
                car_current_solar_usage = min(car_charging_power, solar_surplus)
                remaining_solar = solar_surplus - car_current_solar_usage
            else:
                car_current_solar_usage = 0
                remaining_solar = solar_surplus
                
            return {
                "solar_for_batteries": 0,
                "solar_for_car": 0,
                "car_current_solar_usage": car_current_solar_usage,
                "remaining_solar": remaining_solar,
                "total_allocated": car_current_solar_usage,
                "allocation_reason": f"Insufficient solar surplus ({solar_surplus}W ≤ {significant_solar_threshold}W) - car using {car_current_solar_usage}W, {remaining_solar}W remaining"
            }
        
        available_solar = solar_surplus
        solar_for_batteries = 0
        solar_for_car = 0
        
        # Check if batteries can use solar (not full and solar available)
        batteries_full = battery_analysis.get("batteries_full", False)
        average_soc = battery_analysis.get("average_soc", 0)
        max_soc_threshold = battery_analysis.get("max_soc_threshold", 80)
        
        # Account for solar power already being consumed by car
        car_charging_power = power_analysis.get("car_charging_power", 0)
        if car_charging_power > 100:  # Car is actually charging
            # Assume car is already consuming some solar if surplus exists
            car_current_solar_usage = min(car_charging_power, available_solar)
            available_solar -= car_current_solar_usage
        else:
            car_current_solar_usage = 0
        
        # Priority 1: Allocate remaining solar to batteries if they need charging
        if not batteries_full and average_soc < max_soc_threshold and available_solar > 0:
            # Estimate actual battery charging needs based on SOC and capacity
            soc_deficit = max_soc_threshold - average_soc  # % points needed
            # Rough estimation: 1% SOC ≈ 100-200W for typical 10-20kWh batteries
            estimated_battery_need = min(available_solar, int(soc_deficit * 100), significant_solar_threshold)
            solar_for_batteries = estimated_battery_need
            available_solar -= solar_for_batteries
        else:
            solar_for_batteries = 0
        
        # Priority 2: Allocate remaining solar to car if batteries are near full or already allocated
        if available_solar > 0 and average_soc >= max_soc_threshold - 10:  # Allow car when batteries >70%
            solar_for_car = available_solar
            available_solar = 0
        
        # Power budget validation
        total_allocated = solar_for_batteries + solar_for_car + car_current_solar_usage
        if total_allocated > solar_surplus:
            _LOGGER.warning("Power allocation exceeds available solar: %dW allocated vs %dW available", 
                          total_allocated, solar_surplus)
            # Scale down proportionally (preserve car current usage first)
            available_for_scaling = solar_surplus - car_current_solar_usage
            new_total = solar_for_batteries + solar_for_car
            if new_total > 0 and available_for_scaling >= 0:
                scale_factor = available_for_scaling / new_total
                solar_for_batteries = int(solar_for_batteries * scale_factor)
                solar_for_car = int(solar_for_car * scale_factor)
            else:
                solar_for_batteries = 0
                solar_for_car = 0
            available_solar = solar_surplus - solar_for_batteries - solar_for_car - car_current_solar_usage
        
        return {
            "solar_for_batteries": solar_for_batteries,
            "solar_for_car": solar_for_car, 
            "car_current_solar_usage": car_current_solar_usage,
            "remaining_solar": available_solar,
            "total_allocated": solar_for_batteries + solar_for_car + car_current_solar_usage,
            "allocation_reason": f"Car already using {car_current_solar_usage}W, allocated {solar_for_batteries}W to batteries, {solar_for_car}W additional to car, {available_solar}W remaining"
        }

    async def _analyze_solar_forecast(self, data: dict[str, Any]) -> dict[str, Any]:
        """Analyze tomorrow's solar production potential based on weather forecast."""
        weather_entity = self.config.get(CONF_WEATHER_ENTITY)
        
        if not weather_entity:
            return {
                "forecast_available": False, 
                "solar_production_factor": 0.5,  # Neutral assumption
                "expected_solar_production": "unknown",
                "reason": "No weather entity configured"
            }
        
        state = data.get("weather_state")
        if not state or not hasattr(state, 'attributes'):
            return {
                "forecast_available": False,
                "solar_production_factor": 0.5,
                "expected_solar_production": "unknown", 
                "reason": "Weather entity unavailable"
            }
        
        # Try to get forecast from attributes first (legacy)
        forecast = state.attributes.get('forecast', [])
        
        # If no forecast in attributes, try weather.get_forecasts service (modern HA)
        if not forecast:
            try:
                response = await self.hass.services.async_call(
                    "weather",
                    "get_forecasts",
                    {
                        "entity_id": weather_entity,
                        "type": "hourly"
                    },
                    blocking=True,
                    return_response=True
                )
                if response and weather_entity in response:
                    forecast = response[weather_entity].get("forecast", [])
            except Exception as e:
                _LOGGER.debug("Failed to get weather forecast via service: %s", e)
        
        if not forecast:
            return {
                "forecast_available": False,
                "solar_production_factor": 0.5,
                "expected_solar_production": "unknown",
                "reason": "No forecast data available from attributes or service"
            }
        
        now = datetime.now()
        tomorrow_forecasts = []
        
        for f in forecast:
            if not f.get('datetime'):
                continue
            try:
                forecast_time = datetime.fromisoformat(f['datetime'].replace('Z', '+00:00'))
                # Get forecasts for next 24 hours
                if now < forecast_time <= now + timedelta(hours=24):
                    tomorrow_forecasts.append(f)
            except (ValueError, TypeError):
                continue
        
        if not tomorrow_forecasts:
            return {
                "forecast_available": False,
                "solar_production_factor": 0.5,
                "expected_solar_production": "unknown",
                "reason": "No valid forecast data for next 24h"
            }
        
        # Analyze cloud coverage and precipitation for solar potential
        sunny_hours = 0
        partly_cloudy_hours = 0
        cloudy_hours = 0
        
        for forecast_hour in tomorrow_forecasts:
            condition = forecast_hour.get('condition', '').lower()
            precipitation = forecast_hour.get('precipitation', 0) or 0
            
            # Only consider daylight hours (6 AM to 8 PM)
            try:
                forecast_time = datetime.fromisoformat(forecast_hour['datetime'].replace('Z', '+00:00'))
                if 6 <= forecast_time.hour <= 20:
                    if precipitation > 2:  # Heavy rain/snow
                        cloudy_hours += 1
                    elif condition in ['sunny', 'clear']:
                        sunny_hours += 1
                    elif condition in ['partlycloudy', 'cloudy']:
                        if precipitation < 0.5:
                            partly_cloudy_hours += 1
                        else:
                            cloudy_hours += 1
                    else:
                        cloudy_hours += 1
            except (ValueError, TypeError):
                cloudy_hours += 1  # Conservative assumption
        
        total_daylight_hours = sunny_hours + partly_cloudy_hours + cloudy_hours
        if total_daylight_hours == 0:
            return {
                "forecast_available": False,
                "solar_production_factor": 0.5,
                "expected_solar_production": "unknown",
                "reason": "No daylight hours in forecast"
            }
        
        # Calculate expected solar production relative to perfect day
        solar_production_factor = (
            (sunny_hours * 1.0) +           # 100% production
            (partly_cloudy_hours * 0.6) +  # 60% production  
            (cloudy_hours * 0.2)           # 20% production
        ) / total_daylight_hours
        
        expected_production = (
            "excellent" if solar_production_factor > 0.8 else
            "good" if solar_production_factor > 0.6 else
            "moderate" if solar_production_factor > 0.3 else
            "poor"
        )
        
        return {
            "forecast_available": True,
            "sunny_hours": sunny_hours,
            "partly_cloudy_hours": partly_cloudy_hours,
            "cloudy_hours": cloudy_hours,
            "total_daylight_hours": total_daylight_hours,
            "solar_production_factor": solar_production_factor,  # 0.0 to 1.0
            "expected_solar_production": expected_production,
            "reason": f"Forecast: {sunny_hours}h sunny, {partly_cloudy_hours}h partly cloudy, {cloudy_hours}h cloudy"
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

    def _get_time_context(self) -> dict[str, Any]:
        """Get time-of-day context for charging decisions."""
        now = datetime.now()
        hour = now.hour
        
        return {
            "current_hour": hour,
            "is_night": 22 <= hour or hour <= 6,      # 10 PM - 6 AM  
            "is_early_morning": 6 < hour <= 9,        # 6 AM - 9 AM
            "is_solar_peak": 10 <= hour <= 16,        # 10 AM - 4 PM
            "is_evening": 17 <= hour <= 21,           # 5 PM - 9 PM
            "hours_until_sunrise": max(0, (6 - hour) % 24),
            "winter_season": now.month in [11, 12, 1, 2]  # Shorter days
        }

    def _decide_battery_grid_charging_enhanced(
        self,
        price_analysis: dict[str, Any],
        battery_analysis: dict[str, Any],
        power_allocation: dict[str, Any],
        solar_forecast: dict[str, Any],
        time_context: dict[str, Any],
    ) -> dict[str, Any]:
        """Enhanced battery charging decision with solar forecasting."""
        # Safety checks first
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
        
        # Get key values
        average_soc = battery_analysis.get("average_soc", 0)
        current_price = price_analysis.get("current_price")
        is_low_price = price_analysis.get("is_low_price", False)
        very_low_price = price_analysis.get("very_low_price", False)
        solar_surplus = power_allocation.get("remaining_solar", 0)
        solar_forecast_factor = solar_forecast.get("solar_production_factor", 0.5)
        
        # Get configurable thresholds
        emergency_soc = self.config.get(CONF_EMERGENCY_SOC_THRESHOLD, DEFAULT_EMERGENCY_SOC)
        significant_solar_threshold = self.config.get(CONF_SIGNIFICANT_SOLAR_THRESHOLD, DEFAULT_SIGNIFICANT_SOLAR_THRESHOLD)
        poor_forecast_threshold = self.config.get(CONF_POOR_SOLAR_FORECAST_THRESHOLD, DEFAULT_POOR_SOLAR_FORECAST) / 100.0
        excellent_forecast_threshold = self.config.get(CONF_EXCELLENT_SOLAR_FORECAST_THRESHOLD, DEFAULT_EXCELLENT_SOLAR_FORECAST) / 100.0
        
        # 1. EMERGENCY: Always charge if critically low (true emergency overrides price)
        if average_soc < emergency_soc:
            return {
                "battery_grid_charging": True,
                "battery_grid_charging_reason": f"Emergency charge - SOC {average_soc:.0f}% < {emergency_soc}% threshold, charging regardless of price ({current_price:.3f}€/kWh)",
            }
        
        # 2. SOLAR PRIORITY: Use allocated solar power (prevents race conditions)
        allocated_solar = power_allocation.get("solar_for_batteries", 0)
        if allocated_solar > 0:
            return {
                "battery_grid_charging": False,
                "battery_grid_charging_reason": f"Using allocated solar power ({allocated_solar}W) for batteries instead of grid",
            }
        
        # 3. VERY LOW PRICE: Always charge (configurable threshold of daily range)
        if very_low_price:
            very_low_threshold_percent = self.config.get(CONF_VERY_LOW_PRICE_THRESHOLD, DEFAULT_VERY_LOW_PRICE_THRESHOLD)
            return {
                "battery_grid_charging": True,
                "battery_grid_charging_reason": f"Very low price ({current_price:.3f}€/kWh) - bottom {very_low_threshold_percent}% of daily range",
            }
        
        # 4. TIME-AWARE & FORECAST-AWARE CHARGING: Main logic enhancement
        is_night = time_context.get("is_night", False)
        is_solar_peak = time_context.get("is_solar_peak", False)
        winter_season = time_context.get("winter_season", False)
        
        # Predictive price logic: skip charging if significant price drop expected
        significant_price_drop = price_analysis.get("significant_price_drop", False)
        if is_low_price and significant_price_drop and average_soc > 20:
            return {
                "battery_grid_charging": False,
                "battery_grid_charging_reason": f"SOC {average_soc:.0f}% sufficient - waiting for significant price drop next hour ({price_analysis.get('next_price', '?'):.3f}€/kWh)",
            }
        
        if is_low_price:
            # Time-of-day priority logic (avoid conflicts)
            
            # Solar peak hours: Be conservative if solar is expected (highest priority)
            if is_solar_peak and average_soc > 30 and solar_forecast_factor > 0.6:
                return {
                    "battery_grid_charging": False,
                    "battery_grid_charging_reason": f"Solar peak hours - SOC {average_soc:.0f}% sufficient, awaiting solar production",
                }
            
            # Night + Winter: Most aggressive (both conditions)
            elif is_night and winter_season and average_soc < 60:
                return {
                    "battery_grid_charging": True,
                    "battery_grid_charging_reason": f"Night + Winter charging - SOC {average_soc:.0f}% at low price ({current_price:.3f}€/kWh) during off-peak winter hours",
                }
            
            # Night charging: More aggressive during off-peak hours  
            elif is_night and average_soc < 60:
                return {
                    "battery_grid_charging": True,
                    "battery_grid_charging_reason": f"Night charging - SOC {average_soc:.0f}% at low price ({current_price:.3f}€/kWh) during off-peak hours",
                }
                
            # Winter season: More aggressive due to shorter days (only if not night)
            elif winter_season and average_soc < 50:
                return {
                    "battery_grid_charging": True,
                    "battery_grid_charging_reason": f"Winter season - SOC {average_soc:.0f}% < 50% with low price ({current_price:.3f}€/kWh)",
                }
            
            # Restructured logic to avoid overlaps - order matters!
            
            # CRITICAL LOW: SOC < 30% = always charge (override forecasts)
            if average_soc < 30:
                return {
                    "battery_grid_charging": True,
                    "battery_grid_charging_reason": f"Critical SOC {average_soc:.0f}% < 30% - charge despite {solar_forecast.get('expected_solar_production', 'any')} solar forecast",
                }
            
            # LOW + POOR FORECAST: 30% ≤ SOC < 40% + poor forecast = charge
            elif average_soc < 40 and solar_forecast_factor < poor_forecast_threshold:
                return {
                    "battery_grid_charging": True,
                    "battery_grid_charging_reason": f"Low SOC {average_soc:.0f}% + {solar_forecast.get('expected_solar_production', 'poor')} solar forecast ({solar_forecast_factor:.0%} < {poor_forecast_threshold:.0%}) - charge while price low",
                }
            
            # MEDIUM + EXCELLENT: 30% ≤ SOC ≤ 60% + excellent forecast = skip
            elif 30 <= average_soc <= 60 and solar_forecast_factor > excellent_forecast_threshold:
                return {
                    "battery_grid_charging": False,
                    "battery_grid_charging_reason": f"SOC {average_soc:.0f}% sufficient + {solar_forecast.get('expected_solar_production', 'excellent')} solar forecast ({solar_forecast_factor:.0%} > {excellent_forecast_threshold:.0%})",
                }
            
            # MEDIUM: SOC < 50% = charge (default for medium levels)
            elif average_soc < 50:
                return {
                    "battery_grid_charging": True,
                    "battery_grid_charging_reason": f"Medium SOC {average_soc:.0f}% < 50% + {solar_forecast.get('expected_solar_production', 'moderate')} conditions - charge at low price",
                }
        
        # 5. DEFAULT: Price not favorable
        current = price_analysis.get("current_price")
        position = price_analysis.get("price_position", 0.5)
        return {
            "battery_grid_charging": False,
            "battery_grid_charging_reason": f"Price not favorable ({current:.3f}€/kWh, {position:.0%} of daily range) for SOC {average_soc:.0f}%",
        }


    def _decide_car_grid_charging(
        self,
        price_analysis: dict[str, Any],
        battery_analysis: dict[str, Any],
        power_analysis: dict[str, Any],
    ) -> dict[str, Any]:
        """Decide whether to charge car from grid or solar based on price analysis and battery status."""
        if not price_analysis.get("data_available", True):
            return {
                "car_grid_charging": False,
                "car_grid_charging_reason": "No price data available",
            }

        allocated_solar = power_analysis.get("solar_for_car", 0)
        average_soc = battery_analysis.get("average_soc")
        max_soc_threshold = battery_analysis.get("max_soc_threshold", 80)

        # Special case: Solar power allocated for car = enable solar-only car charging
        if allocated_solar > 0:
            return {
                "car_grid_charging": True,  # This will be solar-only via charger limit logic
                "car_solar_only": True,  # Flag for clarity
                "car_grid_charging_reason": f"Using allocated solar power ({allocated_solar}W) for car charging, no grid usage",
            }

        # Add predictive logic for car charging too
        significant_price_drop = price_analysis.get("significant_price_drop", False)
        is_low_price = price_analysis.get("is_low_price", False)
        
        # Skip charging if significant price drop expected (even if currently low price)
        if is_low_price and significant_price_drop:
            return {
                "car_grid_charging": False,
                "car_grid_charging_reason": f"Waiting for significant price drop next hour ({price_analysis.get('next_price', '?'):.3f}€/kWh) - better than current ({price_analysis.get('current_price', '?'):.3f}€/kWh)",
            }

        if not is_low_price:
            current = price_analysis.get("current_price")
            threshold = price_analysis.get("price_threshold")
            return {
                "car_grid_charging": False,
                "car_grid_charging_reason": f"Price too high ({current:.3f}€/kWh) - threshold: {threshold:.3f}€/kWh",
            }

        if price_analysis.get("very_low_price"):
            current = price_analysis.get("current_price")
            very_low_threshold_percent = self.config.get(CONF_VERY_LOW_PRICE_THRESHOLD, DEFAULT_VERY_LOW_PRICE_THRESHOLD)
            return {
                "car_grid_charging": True,
                "car_grid_charging_reason": f"Very low price ({current:.3f}€/kWh) - bottom {very_low_threshold_percent}% of daily range",
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
        power_allocation: dict[str, Any],
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Calculate optimal charger power limit based on energy management scenario."""
        car_charging_power = data.get("car_charging_power", 0)  # Get from original data
        
        if car_charging_power <= 0:
            return {
                "charger_limit": 0,
                "charger_limit_reason": "Car not currently charging",
            }

        # Use allocated solar power instead of raw surplus
        allocated_solar_for_car = power_allocation.get("solar_for_car", 0)
        original_solar_surplus = data.get("solar_surplus", 0)  # Keep for reference
        monthly_grid_peak = data.get("monthly_grid_peak", 0)
        # Use consistent 90% safety margin logic
        base_grid_setpoint = 2500  # Conservative base limit
        if monthly_grid_peak and monthly_grid_peak > base_grid_setpoint:
            # Use 90% of monthly peak to avoid exceeding it
            max_grid_setpoint = int(monthly_grid_peak * 0.9)
        else:
            max_grid_setpoint = base_grid_setpoint
        average_soc = battery_analysis.get("average_soc")
        
        # If battery data is unavailable, use conservative approach
        if average_soc is None:
            charger_limit = min(max_grid_setpoint, 11000)
            return {
                "charger_limit": int(charger_limit),
                "charger_limit_reason": f"Battery data unavailable - conservative limit ({int(charger_limit)}W)",
            }
        
        max_soc_threshold = battery_analysis.get("max_soc_threshold", 80)
        car_solar_only = data.get("car_solar_only", False)
        
        # Use allocated solar power for solar-only car charging (replaces redundant logic)
        if car_solar_only and allocated_solar_for_car > 0:
            charger_limit = min(allocated_solar_for_car, 11000)
            return {
                "charger_limit": int(charger_limit),
                "charger_limit_reason": f"Solar-only car charging - limited to allocated solar power ({int(charger_limit)}W), no grid usage",
            }
        
        # If battery < max_soc_threshold: Car gets grid setpoint only, surplus goes to batteries
        if average_soc < max_soc_threshold:
            charger_limit = min(max_grid_setpoint, 11000)
            return {
                "charger_limit": int(charger_limit),
                "charger_limit_reason": f"Battery {average_soc:.0f}% < {max_soc_threshold}% - car limited to grid setpoint ({int(charger_limit)}W), surplus for batteries",
            }
        
        # If battery ≥ max_soc_threshold: Car can use surplus + grid setpoint
        available_power = original_solar_surplus + max_grid_setpoint
        charger_limit = min(available_power, 11000)
        return {
            "charger_limit": int(charger_limit),
            "charger_limit_reason": f"Battery {average_soc:.0f}% ≥ {max_soc_threshold}% - car can use surplus + grid ({int(charger_limit)}W = {original_solar_surplus}W + {max_grid_setpoint}W)",
        }

    def _calculate_grid_setpoint(
        self,
        price_analysis: dict[str, Any],
        battery_analysis: dict[str, Any],
        power_allocation: dict[str, Any],
        data: dict[str, Any],
        charger_limit: int,
    ) -> dict[str, Any]:
        """Calculate grid setpoint based on energy management scenario."""
        car_charging_power = data.get("car_charging_power", 0)  # Original data
        allocated_solar_for_car = power_allocation.get("solar_for_car", 0)  # Allocated power
        monthly_grid_peak = data.get("monthly_grid_peak", 0)
        battery_grid_charging = data.get("battery_grid_charging", False)  # Now has fresh data
        average_soc = battery_analysis.get("average_soc")
        
        # Ignore car charging power below 100W (standby/measurement noise)
        significant_car_charging = car_charging_power >= 100
        
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
        
        # Determine maximum grid setpoint based on monthly peak with safety margin
        base_grid_setpoint = 2500  # Conservative base limit
        if monthly_grid_peak and monthly_grid_peak > base_grid_setpoint:
            # Use 90% of monthly peak to avoid exceeding it
            max_grid_setpoint = int(monthly_grid_peak * 0.9)
        else:
            max_grid_setpoint = base_grid_setpoint
        
        # Use car_solar_only flag instead of redundant logic  
        max_soc_threshold = battery_analysis.get("max_soc_threshold", 80)
        car_solar_only = data.get("car_solar_only", False)
        
        if significant_car_charging and car_solar_only:
            return {
                "grid_setpoint": 0,
                "grid_setpoint_reason": f"Solar-only car charging detected - grid setpoint 0W",
            }
        
        # Case 1: Car charging + battery < max_soc_threshold - grid for car consumption, surplus for batteries
        if significant_car_charging and average_soc < max_soc_threshold:
            # Grid setpoint follows actual car consumption up to the charger limit and grid peak limit
            car_grid_need = min(car_charging_power, charger_limit, max_grid_setpoint)
            grid_setpoint = car_grid_need
            return {
                "grid_setpoint": int(grid_setpoint),
                "grid_setpoint_reason": f"Car drawing {car_charging_power}W, battery {average_soc:.0f}% < {max_soc_threshold}% - grid for car ({int(grid_setpoint)}W), surplus for batteries",
            }
        
        # Case 2: Car charging + battery ≥ max_soc_threshold - grid supports car, can also charge batteries if decided
        if significant_car_charging and average_soc >= max_soc_threshold:
            # Grid covers car needs first - use allocated solar power
            car_available_solar = allocated_solar_for_car + power_allocation.get("car_current_solar_usage", 0)
            car_grid_need = max(0, car_charging_power - car_available_solar)
            # Ensure within safety limits
            car_grid_need = min(car_grid_need, max_grid_setpoint)
            
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
                    "grid_setpoint_reason": f"Car drawing {car_charging_power}W, battery {average_soc:.0f}% ≥ {max_soc_threshold}% - grid covers car deficit ({int(grid_setpoint)}W = {car_charging_power}W - {power_analysis.get('solar_surplus', 0)}W surplus)",
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