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
    CONF_WEATHER_ENTITY,
    CONF_EMERGENCY_SOC_THRESHOLD,
    CONF_VERY_LOW_PRICE_THRESHOLD,
    CONF_SIGNIFICANT_SOLAR_THRESHOLD,
    CONF_POOR_SOLAR_FORECAST_THRESHOLD,
    CONF_EXCELLENT_SOLAR_FORECAST_THRESHOLD,
    DEFAULT_MIN_SOC,
    DEFAULT_MAX_SOC,
    DEFAULT_PRICE_THRESHOLD,
    DEFAULT_EMERGENCY_SOC,
    DEFAULT_VERY_LOW_PRICE_THRESHOLD,
    DEFAULT_SIGNIFICANT_SOLAR_THRESHOLD,
    DEFAULT_POOR_SOLAR_FORECAST,
    DEFAULT_EXCELLENT_SOLAR_FORECAST,
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

        solar_forecast = self._analyze_solar_forecast(data)
        decision_data["solar_forecast"] = solar_forecast

        battery_decision = self._decide_battery_grid_charging_enhanced(
            price_analysis, battery_analysis, power_analysis, solar_forecast
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
            "forecast": None,  # Could be expanded later with forecast data
            "has_good_forecast": False,  # Could be expanded later
        }

    def _analyze_solar_forecast(self, data: dict[str, Any]) -> dict[str, Any]:
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
        
        forecast = state.attributes.get('forecast', [])
        if not forecast:
            return {
                "forecast_available": False,
                "solar_production_factor": 0.5,
                "expected_solar_production": "unknown",
                "reason": "No forecast data available"
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
        power_analysis: dict[str, Any],
        solar_forecast: dict[str, Any],
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
        solar_surplus = power_analysis.get("solar_surplus", 0)
        solar_forecast_factor = solar_forecast.get("solar_production_factor", 0.5)
        
        # Get configurable thresholds
        emergency_soc = self.config.get(CONF_EMERGENCY_SOC_THRESHOLD, DEFAULT_EMERGENCY_SOC)
        significant_solar_threshold = self.config.get(CONF_SIGNIFICANT_SOLAR_THRESHOLD, DEFAULT_SIGNIFICANT_SOLAR_THRESHOLD)
        poor_forecast_threshold = self.config.get(CONF_POOR_SOLAR_FORECAST_THRESHOLD, DEFAULT_POOR_SOLAR_FORECAST) / 100.0
        excellent_forecast_threshold = self.config.get(CONF_EXCELLENT_SOLAR_FORECAST_THRESHOLD, DEFAULT_EXCELLENT_SOLAR_FORECAST) / 100.0
        
        # 1. EMERGENCY: Always charge if critically low (but respect price limits)
        if average_soc < emergency_soc:
            if is_low_price:
                return {
                    "battery_grid_charging": True,
                    "battery_grid_charging_reason": f"Emergency charge - SOC {average_soc:.0f}% < {emergency_soc}% with acceptable price ({current_price:.3f}€/kWh)",
                }
            else:
                return {
                    "battery_grid_charging": False,
                    "battery_grid_charging_reason": f"Emergency level ({average_soc:.0f}%) but price too high ({current_price:.3f}€/kWh)",
                }
        
        # 2. SOLAR PRIORITY: Never use grid when significant solar available (unless very low price)
        if solar_surplus > significant_solar_threshold and not very_low_price:
            return {
                "battery_grid_charging": False,
                "battery_grid_charging_reason": f"Solar surplus {solar_surplus}W > {significant_solar_threshold}W threshold - use solar instead of grid",
            }
        
        # 3. VERY LOW PRICE: Always charge (configurable threshold of daily range)
        if very_low_price:
            very_low_threshold_percent = self.config.get(CONF_VERY_LOW_PRICE_THRESHOLD, DEFAULT_VERY_LOW_PRICE_THRESHOLD)
            return {
                "battery_grid_charging": True,
                "battery_grid_charging_reason": f"Very low price ({current_price:.3f}€/kWh) - bottom {very_low_threshold_percent}% of daily range",
            }
        
        # 4. FORECAST-AWARE CHARGING: Main logic enhancement using configurable thresholds
        if is_low_price:
            # Low SOC + poor solar forecast = charge now
            if average_soc < 40 and solar_forecast_factor < poor_forecast_threshold:
                return {
                    "battery_grid_charging": True,
                    "battery_grid_charging_reason": f"SOC {average_soc:.0f}% + {solar_forecast.get('expected_solar_production', 'poor')} solar forecast ({solar_forecast_factor:.0%} < {poor_forecast_threshold:.0%}) - charge while price low",
                }
            
            # Medium SOC + excellent solar forecast = skip charging  
            if 30 <= average_soc <= 60 and solar_forecast_factor > excellent_forecast_threshold:
                return {
                    "battery_grid_charging": False,
                    "battery_grid_charging_reason": f"SOC {average_soc:.0f}% sufficient + {solar_forecast.get('expected_solar_production', 'excellent')} solar forecast ({solar_forecast_factor:.0%} > {excellent_forecast_threshold:.0%})",
                }
            
            # Low SOC but good solar expected = still charge (conservative)
            if average_soc < 30:
                return {
                    "battery_grid_charging": True,
                    "battery_grid_charging_reason": f"SOC {average_soc:.0f}% low - charge despite {solar_forecast.get('expected_solar_production', 'good')} solar forecast",
                }
            
            # Medium SOC + moderate forecast = charge
            if average_soc < 50:
                return {
                    "battery_grid_charging": True,
                    "battery_grid_charging_reason": f"SOC {average_soc:.0f}% + {solar_forecast.get('expected_solar_production', 'moderate')} conditions - charge at low price",
                }
        
        # 5. DEFAULT: Price not favorable
        current = price_analysis.get("current_price")
        position = price_analysis.get("price_position", 0.5)
        return {
            "battery_grid_charging": False,
            "battery_grid_charging_reason": f"Price not favorable ({current:.3f}€/kWh, {position:.0%} of daily range) for SOC {average_soc:.0f}%",
        }

    def _decide_battery_grid_charging(
        self,
        price_analysis: dict[str, Any],
        battery_analysis: dict[str, Any],
        power_analysis: dict[str, Any],
    ) -> dict[str, Any]:
        """Legacy battery charging decision - kept for compatibility."""
        # This function is kept but no longer used - replaced by enhanced version
        return self._decide_battery_grid_charging_enhanced(
            price_analysis, battery_analysis, power_analysis, {"solar_production_factor": 0.5}
        )

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