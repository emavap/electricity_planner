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
    CONF_SOLAR_FORECAST_CURRENT_ENTITY,
    CONF_SOLAR_FORECAST_NEXT_ENTITY,
    CONF_SOLAR_FORECAST_TODAY_ENTITY,
    CONF_SOLAR_FORECAST_REMAINING_TODAY_ENTITY,
    CONF_SOLAR_FORECAST_TOMORROW_ENTITY,
    CONF_BATTERY_CAPACITIES,
    CONF_EMERGENCY_SOC_THRESHOLD,
    CONF_VERY_LOW_PRICE_THRESHOLD,
    CONF_SIGNIFICANT_SOLAR_THRESHOLD,
    CONF_POOR_SOLAR_FORECAST_THRESHOLD,
    CONF_EXCELLENT_SOLAR_FORECAST_THRESHOLD,
    CONF_FEEDIN_PRICE_THRESHOLD,
    CONF_MAX_BATTERY_POWER,
    CONF_MAX_CAR_POWER,
    CONF_MAX_GRID_POWER,
    CONF_MIN_CAR_CHARGING_THRESHOLD,
    CONF_EMERGENCY_SOC_OVERRIDE,
    CONF_WINTER_NIGHT_SOC_OVERRIDE,
    CONF_SOLAR_PEAK_EMERGENCY_SOC,
    CONF_PREDICTIVE_CHARGING_MIN_SOC,
    DEFAULT_MIN_SOC,
    DEFAULT_MAX_SOC,
    DEFAULT_PRICE_THRESHOLD,
    DEFAULT_EMERGENCY_SOC,
    DEFAULT_VERY_LOW_PRICE_THRESHOLD,
    DEFAULT_SIGNIFICANT_SOLAR_THRESHOLD,
    DEFAULT_POOR_SOLAR_FORECAST,
    DEFAULT_EXCELLENT_SOLAR_FORECAST,
    DEFAULT_FEEDIN_PRICE_THRESHOLD,
    DEFAULT_MAX_BATTERY_POWER,
    DEFAULT_MAX_CAR_POWER,
    DEFAULT_MAX_GRID_POWER,
    DEFAULT_MIN_CAR_CHARGING_THRESHOLD,
    DEFAULT_EMERGENCY_SOC_OVERRIDE,
    DEFAULT_WINTER_NIGHT_SOC_OVERRIDE,
    DEFAULT_SOLAR_PEAK_EMERGENCY_SOC,
    DEFAULT_PREDICTIVE_CHARGING_MIN_SOC,
    DEFAULT_BASE_GRID_SETPOINT,
    DEFAULT_MONTHLY_PEAK_SAFETY_MARGIN,
    DEFAULT_CRITICAL_SOC_THRESHOLD,
    DEFAULT_MEDIUM_SOC_THRESHOLD,
    DEFAULT_HIGH_SOC_THRESHOLD,
)

_LOGGER = logging.getLogger(__name__)


class ChargingDecisionEngine:
    """Engine for making charging decisions based on multiple factors."""

    def __init__(self, hass: HomeAssistant, config: dict[str, Any]) -> None:
        """Initialize the decision engine."""
        self.hass = hass
        self.config = config

    def _validate_power_value(self, power: float, min_value: float = 0, max_value: float | None = None, name: str = "power") -> float:
        """Validate and clamp power values to safe ranges."""
        if power < min_value:
            _LOGGER.warning("%s value %dW below minimum, clamping to %dW", name, power, min_value)
            return min_value
        if max_value and power > max_value:
            _LOGGER.warning("%s value %dW above maximum, clamping to %dW", name, power, max_value)
            return max_value
        return power

    def _get_max_grid_power(self) -> int:
        """Get maximum allowed grid power with safety margin."""
        return self.config.get(CONF_MAX_GRID_POWER, DEFAULT_MAX_GRID_POWER)

    def _get_safe_grid_setpoint(self, monthly_peak: float | None) -> int:
        """Calculate safe grid setpoint based on monthly peak."""
        base_setpoint = DEFAULT_BASE_GRID_SETPOINT
        if monthly_peak and monthly_peak > base_setpoint:
            return int(monthly_peak * DEFAULT_MONTHLY_PEAK_SAFETY_MARGIN)
        return base_setpoint

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
        """Decide whether to enable solar feed-in based purely on price threshold."""
        if not price_analysis.get("data_available", True):
            return {
                "feedin_solar": False,
                "feedin_solar_reason": "No price data available",
            }

        current_price = price_analysis.get("current_price", 0)
        feedin_threshold = self.config.get(CONF_FEEDIN_PRICE_THRESHOLD, DEFAULT_FEEDIN_PRICE_THRESHOLD)
        remaining_solar = power_allocation.get("remaining_solar", 0)

        # Price-driven decision: Enable feed-in if price meets threshold
        enable_feedin = current_price >= feedin_threshold
        action = "enable" if enable_feedin else "disable"

        return {
            "feedin_solar": enable_feedin,
            "feedin_solar_reason": f"Price {current_price:.3f}€/kWh {'≥' if enable_feedin else '<'} {feedin_threshold:.3f}€/kWh - {action} solar export (surplus: {remaining_solar}W)",
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
        """Analyze current power flow and consumption with separate solar production and house consumption."""
        solar_production = data.get("solar_production")
        house_consumption = data.get("house_consumption")  
        solar_surplus = data.get("solar_surplus")  # Already calculated in coordinator
        car_charging_power = data.get("car_charging_power")

        # Handle unavailable sensors - use 0 as safe default for power calculations
        solar_production = solar_production if solar_production is not None else 0
        house_consumption = house_consumption if house_consumption is not None else 0
        solar_surplus = solar_surplus if solar_surplus is not None else 0
        car_charging_power = car_charging_power if car_charging_power is not None else 0

        # Get configurable solar threshold
        significant_solar_threshold = self.config.get(CONF_SIGNIFICANT_SOLAR_THRESHOLD, DEFAULT_SIGNIFICANT_SOLAR_THRESHOLD)

        # Calculate additional metrics with separate values
        house_consumption_without_car = max(0, house_consumption - car_charging_power)
        solar_coverage_ratio = (solar_production / house_consumption) if house_consumption > 0 else 0

        return {
            "solar_production": solar_production,
            "house_consumption": house_consumption,
            "house_consumption_without_car": house_consumption_without_car,
            "solar_surplus": solar_surplus,  # Available excess solar that can be used for batteries/car/export
            "car_charging_power": car_charging_power,
            "has_solar_production": solar_production > 0,
            "has_solar_surplus": solar_surplus > 0,  # Has available excess solar power
            "significant_solar_surplus": solar_surplus > significant_solar_threshold,
            "car_currently_charging": car_charging_power > 0,
            "available_surplus_for_batteries": max(0, solar_surplus - car_charging_power),  # Surplus after car consumption
            "significant_solar_threshold": significant_solar_threshold,
            "solar_coverage_ratio": solar_coverage_ratio,  # 1.0 = solar covers 100% of house consumption
            "has_excess_solar_available": solar_surplus > 0,  # Available for batteries, car, or grid export
        }

    def _analyze_solar_production(self, data: dict[str, Any]) -> dict[str, Any]:
        """Analyze solar production status with separate production and consumption data."""
        solar_production = data.get("solar_production")
        house_consumption = data.get("house_consumption")
        solar_surplus = data.get("solar_surplus")
        
        solar_production = solar_production if solar_production is not None else 0
        house_consumption = house_consumption if house_consumption is not None else 0
        solar_surplus = solar_surplus if solar_surplus is not None else 0

        # More detailed solar analysis with separate values
        is_producing = solar_production > 0
        has_available_surplus = solar_surplus > 0  # Has excess that could be used for batteries/car/export
        production_efficiency = min(1.0, solar_production / 5000) if solar_production > 0 else 0  # Assume 5kW max

        return {
            "current_production": solar_production,
            "house_consumption": house_consumption,
            "available_surplus": solar_surplus,  # Available for batteries, car charging, or grid export
            "is_producing": is_producing,
            "has_available_surplus": has_available_surplus,  # Can allocate to batteries/car/export
            "production_efficiency": production_efficiency,  # 0.0-1.0 relative to typical max
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

        # Validate solar surplus
        solar_surplus = self._validate_power_value(solar_surplus, name="solar_surplus")

        if solar_surplus <= significant_solar_threshold:
            # Even with insufficient solar, account for current car consumption
            car_charging_power = power_analysis.get("car_charging_power", 0)
            min_car_charging_threshold = self.config.get(CONF_MIN_CAR_CHARGING_THRESHOLD, DEFAULT_MIN_CAR_CHARGING_THRESHOLD)
            if car_charging_power > min_car_charging_threshold:  # Car is actually charging
                car_current_solar_usage = min(car_charging_power, solar_surplus)
                remaining_solar = max(0, solar_surplus - car_current_solar_usage)
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

        # CRITICAL FIX: Track actual solar consumption more precisely
        car_charging_power = power_analysis.get("car_charging_power", 0)
        car_current_solar_usage = 0

        # Only count car as using solar if it's actually charging AND there's solar available
        min_car_charging_threshold = self.config.get(CONF_MIN_CAR_CHARGING_THRESHOLD, DEFAULT_MIN_CAR_CHARGING_THRESHOLD)
        if car_charging_power > min_car_charging_threshold and solar_surplus > 0:
            # Car can only use what's actually available
            car_current_solar_usage = min(car_charging_power, solar_surplus)

        # Available solar after current car consumption
        available_solar = max(0, solar_surplus - car_current_solar_usage)
        solar_for_batteries = 0
        solar_for_car = 0

        # Check if batteries can use solar (not full and solar available)
        batteries_full = battery_analysis.get("batteries_full", False)
        average_soc = battery_analysis.get("average_soc", 0)
        max_soc_threshold = battery_analysis.get("max_soc_threshold", 80)

        # Priority 1: Allocate solar to batteries if they need charging (with safety margin)
        battery_needs_solar = (
            not batteries_full and
            average_soc < max_soc_threshold - 5 and  # 5% safety margin to prevent waste
            available_solar > 0
        )

        if battery_needs_solar:
            soc_deficit = max(0, max_soc_threshold - average_soc)
            estimated_battery_need = min(
                available_solar,
                int(soc_deficit * 100),  # 1% SOC ≈ 100W estimation
                significant_solar_threshold,
                self.config.get(CONF_MAX_BATTERY_POWER, DEFAULT_MAX_BATTERY_POWER)
            )
            solar_for_batteries = max(0, estimated_battery_need)
            available_solar = max(0, available_solar - solar_for_batteries)

        # Priority 2: Allocate remaining solar to car if batteries are near full
        if available_solar > 0 and average_soc >= max_soc_threshold - 10:
            solar_for_car = min(
                available_solar,
                self.config.get(CONF_MAX_CAR_POWER, DEFAULT_MAX_CAR_POWER)
            )
            available_solar = max(0, available_solar - solar_for_car)

        # Validate total power allocation doesn't exceed available solar
        total_allocated = solar_for_batteries + solar_for_car + car_current_solar_usage
        if total_allocated > solar_surplus:
            _LOGGER.warning("Power allocation %dW exceeds available solar %dW - applying proportional reduction",
                          total_allocated, solar_surplus)
            if total_allocated > 0:
                scale_factor = solar_surplus / total_allocated
                solar_for_batteries = int(solar_for_batteries * scale_factor)
                solar_for_car = int(solar_for_car * scale_factor)
                car_current_solar_usage = int(car_current_solar_usage * scale_factor)

        remaining_solar = max(0, solar_surplus - solar_for_batteries - solar_for_car - car_current_solar_usage)

        return {
            "solar_for_batteries": solar_for_batteries,
            "solar_for_car": solar_for_car,
            "car_current_solar_usage": car_current_solar_usage,
            "remaining_solar": remaining_solar,
            "total_allocated": solar_for_batteries + solar_for_car + car_current_solar_usage,
            "allocation_reason": f"Car using {car_current_solar_usage}W, allocated {solar_for_batteries}W to batteries, {solar_for_car}W additional to car, {remaining_solar}W remaining (total: {solar_surplus}W)"
        }

    async def _analyze_solar_forecast(self, data: dict[str, Any]) -> dict[str, Any]:
        """Analyze solar production potential based on dedicated forecast entities."""
        # Get forecast values from entities 
        forecast_current = data.get("solar_forecast_current")
        forecast_next = data.get("solar_forecast_next") 
        forecast_today = data.get("solar_forecast_today")
        forecast_remaining_today = data.get("solar_forecast_remaining_today")
        forecast_tomorrow = data.get("solar_forecast_tomorrow")

        # Check if any forecast entities are configured
        has_forecast_entities = any([
            self.config.get(CONF_SOLAR_FORECAST_CURRENT_ENTITY),
            self.config.get(CONF_SOLAR_FORECAST_NEXT_ENTITY),
            self.config.get(CONF_SOLAR_FORECAST_TODAY_ENTITY),
            self.config.get(CONF_SOLAR_FORECAST_REMAINING_TODAY_ENTITY),
            self.config.get(CONF_SOLAR_FORECAST_TOMORROW_ENTITY)
        ])

        # If no forecast entities configured, use safe defaults
        if not has_forecast_entities:
            return {
                "forecast_available": False,
                "solar_production_factor": 0.5,
                "expected_solar_production": "unknown",
                "reason": "No solar forecast entities configured"
            }

        # Use the new forecast entities  
        if forecast_tomorrow is not None and forecast_today is not None:
            # Calculate relative performance: tomorrow vs today
            if forecast_today > 0:
                solar_production_factor = min(1.0, forecast_tomorrow / forecast_today)
            else:
                solar_production_factor = 0.5  # Default if today's forecast is 0
                
            # Categorize expected production
            if solar_production_factor > 0.8:
                expected_production = "excellent"
            elif solar_production_factor > 0.6:
                expected_production = "good"
            elif solar_production_factor > 0.3:
                expected_production = "moderate"
            else:
                expected_production = "poor"

            return {
                "forecast_available": True,
                "solar_production_factor": solar_production_factor,
                "expected_solar_production": expected_production,
                "forecast_current_kwh": forecast_current,
                "forecast_next_kwh": forecast_next,
                "forecast_today_kwh": forecast_today,
                "forecast_remaining_today_kwh": forecast_remaining_today, 
                "forecast_tomorrow_kwh": forecast_tomorrow,
                "reason": f"Tomorrow {forecast_tomorrow:.1f}kWh vs today {forecast_today:.1f}kWh (factor: {solar_production_factor:.1%})"
            }
        
        # If only remaining today is available, use that for near-term decisions
        elif forecast_remaining_today is not None:
            # Estimate factor based on remaining production vs typical daily minimum
            typical_minimum_daily = 5.0  # Assume 5kWh minimum for a decent solar day
            solar_production_factor = min(1.0, forecast_remaining_today / typical_minimum_daily)
            
            expected_production = (
                "good" if solar_production_factor > 0.6 else
                "moderate" if solar_production_factor > 0.3 else
                "poor"
            )

            return {
                "forecast_available": True,
                "solar_production_factor": solar_production_factor,
                "expected_solar_production": expected_production,
                "forecast_remaining_today_kwh": forecast_remaining_today,
                "reason": f"Remaining today: {forecast_remaining_today:.1f}kWh"
            }

        # If only current/next hour available, use for immediate decisions
        elif forecast_current is not None or forecast_next is not None:
            current_kwh = forecast_current or 0
            next_kwh = forecast_next or 0
            
            # Use hourly forecasts for immediate solar assessment
            solar_production_factor = min(1.0, max(current_kwh, next_kwh) / 2.0)  # Assume 2kWh/hour is good
            
            expected_production = (
                "good" if solar_production_factor > 0.6 else
                "moderate" if solar_production_factor > 0.3 else
                "poor"
            )

            return {
                "forecast_available": True,
                "solar_production_factor": solar_production_factor,
                "expected_solar_production": expected_production,
                "forecast_current_kwh": current_kwh,
                "forecast_next_kwh": next_kwh,
                "reason": f"Hourly: current {current_kwh:.1f}kWh, next {next_kwh:.1f}kWh"
            }

        # No usable forecast data
        return {
            "forecast_available": False,
            "solar_production_factor": 0.5,
            "expected_solar_production": "unknown",
            "reason": "Solar forecast entities configured but no data available"
        }


    def _analyze_battery_status(self, battery_soc_data: list[dict[str, Any]]) -> dict[str, Any]:
        """Analyze battery status for all configured batteries with capacity-weighted calculations."""
        if not battery_soc_data:
            return {
                "average_soc": None,
                "min_soc": None,
                "max_soc": None,
                "batteries_count": 0,
                "batteries_full": False,
            }

        # Only include batteries with valid SOC values (not unavailable/unknown)
        valid_batteries = [battery for battery in battery_soc_data if "soc" in battery and battery["soc"] is not None]

        # If no valid SOC values are available, return safe defaults
        if not valid_batteries:
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
        battery_capacities = self.config.get(CONF_BATTERY_CAPACITIES, {})

        soc_values = [battery["soc"] for battery in valid_batteries]
        min_soc = min(soc_values)
        max_soc = max(soc_values)

        # Calculate capacity-weighted average SOC if capacities are configured
        if battery_capacities:
            total_stored_energy = 0.0
            total_capacity = 0.0

            for battery in valid_batteries:
                entity_id = battery["entity_id"]
                soc = battery["soc"]
                capacity = battery_capacities.get(entity_id, 10.0)  # Default 10 kWh if not configured

                stored_energy = (soc / 100.0) * capacity
                total_stored_energy += stored_energy
                total_capacity += capacity

                _LOGGER.debug("Battery %s: SOC=%.1f%%, Capacity=%.1fkWh, Stored=%.2fkWh",
                             entity_id, soc, capacity, stored_energy)

            if total_capacity > 0:
                average_soc = (total_stored_energy / total_capacity) * 100.0
                _LOGGER.debug("Capacity-weighted SOC: %.2fkWh/%.2fkWh = %.1f%%",
                             total_stored_energy, total_capacity, average_soc)
            else:
                average_soc = sum(soc_values) / len(soc_values)
                _LOGGER.warning("Total battery capacity is 0, falling back to simple average")
        else:
            # Fallback to simple arithmetic average when no capacities configured
            average_soc = sum(soc_values) / len(soc_values)
            _LOGGER.debug("No battery capacities configured, using simple average SOC: %.1f%%", average_soc)

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
            "capacity_weighted": bool(battery_capacities),
            "total_capacity": sum(battery_capacities.get(battery["entity_id"], 10.0) for battery in valid_batteries) if battery_capacities else None,
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
        remaining_solar = power_allocation.get("remaining_solar", 0)
        if allocated_solar > 0:
            return {
                "battery_grid_charging": False,
                "battery_grid_charging_reason": f"Using allocated solar power ({allocated_solar}W) for batteries instead of grid",
            }

        # Avoid grid charging when batteries are nearly full with solar surplus
        max_soc_threshold = battery_analysis.get("max_soc_threshold", 80)
        if remaining_solar > 0 and average_soc >= max_soc_threshold - 10:
            return {
                "battery_grid_charging": False,
                "battery_grid_charging_reason": f"Battery {average_soc:.0f}% nearly full with {remaining_solar}W solar surplus - preventing solar waste",
            }

        # 3. VERY LOW PRICE: Charge only if it makes sense (not when battery nearly full + good conditions)
        if very_low_price:
            very_low_threshold_percent = self.config.get(CONF_VERY_LOW_PRICE_THRESHOLD, DEFAULT_VERY_LOW_PRICE_THRESHOLD)
            
            # Don't charge from grid if battery nearly full AND good solar conditions
            if average_soc >= max_soc_threshold - 10:  # Within 10% of max
                if remaining_solar > 0:
                    return {
                        "battery_grid_charging": False,
                        "battery_grid_charging_reason": f"Battery nearly full ({average_soc:.0f}%) with {remaining_solar}W solar surplus - avoid grid charging despite very low price",
                    }
                
                # Also check solar forecast - if tomorrow will be excellent, don't charge nearly full battery
                if solar_forecast_factor > 0.8:  # Excellent forecast
                    return {
                        "battery_grid_charging": False,
                        "battery_grid_charging_reason": f"Battery nearly full ({average_soc:.0f}%) + excellent solar forecast ({solar_forecast_factor:.0%}) - skip charging despite very low price",
                    }
            
            return {
                "battery_grid_charging": True,
                "battery_grid_charging_reason": f"Very low price ({current_price:.3f}€/kWh) - bottom {very_low_threshold_percent}% of daily range",
            }

        # 4. TIME-AWARE & FORECAST-AWARE CHARGING: Main logic enhancement
        is_night = time_context.get("is_night", False)
        is_solar_peak = time_context.get("is_solar_peak", False)
        winter_season = time_context.get("winter_season", False)

        # CRITICAL FIX: Emergency charging override for price prediction logic
        # Predictive price logic: skip charging if significant price drop expected, BUT NOT in emergency situations
        significant_price_drop = price_analysis.get("significant_price_drop", False)
        predictive_min_soc = self.config.get(CONF_PREDICTIVE_CHARGING_MIN_SOC, DEFAULT_PREDICTIVE_CHARGING_MIN_SOC)
        if is_low_price and significant_price_drop and average_soc > predictive_min_soc:
            # Additional safety check: don't wait for price drops if SOC is critically low or in winter nights
            # Override price prediction in critical situations
            emergency_soc_override = self.config.get(CONF_EMERGENCY_SOC_OVERRIDE, DEFAULT_EMERGENCY_SOC_OVERRIDE)
            winter_night_soc_override = self.config.get(CONF_WINTER_NIGHT_SOC_OVERRIDE, DEFAULT_WINTER_NIGHT_SOC_OVERRIDE)
            if average_soc < emergency_soc_override or (is_night and winter_season and average_soc < winter_night_soc_override):
                return {
                    "battery_grid_charging": True,
                    "battery_grid_charging_reason": f"Emergency override - SOC {average_soc:.0f}% too low to wait for price drop (winter night: {is_night and winter_season})",
                }

            return {
                "battery_grid_charging": False,
                "battery_grid_charging_reason": f"SOC {average_soc:.0f}% sufficient - waiting for significant price drop next hour ({price_analysis.get('next_price', '?'):.3f}€/kWh)",
            }

        if is_low_price:
            # Time-of-day priority logic (avoid conflicts)

            # Solar peak hours: Be conservative if solar is expected
            if is_solar_peak and average_soc > 30 and solar_forecast_factor > 0.6:
                solar_peak_emergency_soc = self.config.get(CONF_SOLAR_PEAK_EMERGENCY_SOC, DEFAULT_SOLAR_PEAK_EMERGENCY_SOC)
                if average_soc < solar_peak_emergency_soc:
                    return {
                        "battery_grid_charging": True,
                        "battery_grid_charging_reason": f"Emergency override during solar peak - SOC {average_soc:.0f}% < {solar_peak_emergency_soc}% too low to wait for solar",
                    }

                return {
                    "battery_grid_charging": False,
                    "battery_grid_charging_reason": f"Solar peak hours - SOC {average_soc:.0f}% sufficient, awaiting solar production (forecast: {solar_forecast_factor:.0%})",
                }

            # Night + Winter: Most aggressive (both conditions)
            elif is_night and winter_season and average_soc < DEFAULT_HIGH_SOC_THRESHOLD:
                return {
                    "battery_grid_charging": True,
                    "battery_grid_charging_reason": f"Night + Winter charging - SOC {average_soc:.0f}% at low price ({current_price:.3f}€/kWh) during off-peak winter hours",
                }

            # Night charging: More aggressive during off-peak hours
            elif is_night and average_soc < DEFAULT_HIGH_SOC_THRESHOLD:
                return {
                    "battery_grid_charging": True,
                    "battery_grid_charging_reason": f"Night charging - SOC {average_soc:.0f}% at low price ({current_price:.3f}€/kWh) during off-peak hours",
                }

            # Winter season: More aggressive due to shorter days (only if not night)
            elif winter_season and average_soc < DEFAULT_MEDIUM_SOC_THRESHOLD:
                return {
                    "battery_grid_charging": True,
                    "battery_grid_charging_reason": f"Winter season - SOC {average_soc:.0f}% < {DEFAULT_MEDIUM_SOC_THRESHOLD}% with low price ({current_price:.3f}€/kWh)",
                }

            # Restructured logic to avoid overlaps - order matters!

            # CRITICAL LOW: SOC < 30% = always charge (override forecasts)
            if average_soc < 30:
                return {
                    "battery_grid_charging": True,
                    "battery_grid_charging_reason": f"Critical SOC {average_soc:.0f}% < 30% - charge despite {solar_forecast.get('expected_solar_production', 'any')} solar forecast",
                }

            # LOW + POOR FORECAST: low SOC + poor forecast = charge
            elif average_soc < 40 and solar_forecast_factor < poor_forecast_threshold:
                return {
                    "battery_grid_charging": True,
                    "battery_grid_charging_reason": f"Low SOC {average_soc:.0f}% + {solar_forecast.get('expected_solar_production', 'poor')} solar forecast ({solar_forecast_factor:.0%} < {poor_forecast_threshold:.0%}) - charge while price low",
                }

            # MEDIUM + EXCELLENT: threshold range + excellent forecast = skip
            elif DEFAULT_CRITICAL_SOC_THRESHOLD <= average_soc <= DEFAULT_HIGH_SOC_THRESHOLD and solar_forecast_factor > excellent_forecast_threshold:
                return {
                    "battery_grid_charging": False,
                    "battery_grid_charging_reason": f"SOC {average_soc:.0f}% sufficient + {solar_forecast.get('expected_solar_production', 'excellent')} solar forecast ({solar_forecast_factor:.0%} > {excellent_forecast_threshold:.0%})",
                }

            # MEDIUM: SOC below threshold = charge (default for medium levels)
            elif average_soc < DEFAULT_MEDIUM_SOC_THRESHOLD:
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
        min_car_charging_threshold = self.config.get(CONF_MIN_CAR_CHARGING_THRESHOLD, DEFAULT_MIN_CAR_CHARGING_THRESHOLD)

        if car_charging_power <= min_car_charging_threshold:
            return {
                "charger_limit": 0,
                "charger_limit_reason": "Car not currently charging",
            }

        # Use allocated solar power instead of raw surplus
        allocated_solar_for_car = power_allocation.get("solar_for_car", 0)
        available_solar_surplus = power_allocation.get("remaining_solar", 0)
        monthly_grid_peak = data.get("monthly_grid_peak", 0)
        # Calculate safe grid setpoint
        max_grid_setpoint = self._get_safe_grid_setpoint(monthly_grid_peak)
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

        # If battery ≥ max_soc_threshold: Car can use remaining surplus + grid setpoint
        available_power = available_solar_surplus + max_grid_setpoint
        charger_limit = min(available_power, 11000)
        return {
            "charger_limit": int(charger_limit),
            "charger_limit_reason": f"Battery {average_soc:.0f}% ≥ {max_soc_threshold}% - car can use remaining surplus + grid ({int(charger_limit)}W = {available_solar_surplus}W + {max_grid_setpoint}W)",
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

        # Ignore car charging power below threshold (standby/measurement noise)
        min_car_charging_threshold = self.config.get(CONF_MIN_CAR_CHARGING_THRESHOLD, DEFAULT_MIN_CAR_CHARGING_THRESHOLD)
        significant_car_charging = car_charging_power >= min_car_charging_threshold

        # Handle unavailable battery data
        if average_soc is None:
            if significant_car_charging:
                max_grid_setpoint = self._get_safe_grid_setpoint(monthly_grid_peak)
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

        # Determine maximum grid setpoint
        max_grid_setpoint = self._get_safe_grid_setpoint(monthly_grid_peak)

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
            car_grid_need = min(car_charging_power, charger_limit, max_grid_setpoint)
            max_grid_power = self._get_max_grid_power()
            car_grid_need = self._validate_power_value(car_grid_need, 0, max_grid_power, "car_grid_need")
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

            # Validate car grid power need
            max_grid_power = self._get_max_grid_power()
            car_grid_need = self._validate_power_value(car_grid_need, 0, max_grid_power, "car_grid_need")

            # If battery charging decision is on, add battery charging to grid setpoint
            if battery_grid_charging:
                # Grid for car + remaining capacity for batteries
                remaining_grid_capacity = max(0, max_grid_setpoint - car_grid_need)  # Ensure non-negative
                max_battery_power = self.config.get(CONF_MAX_BATTERY_POWER, DEFAULT_MAX_BATTERY_POWER)
                battery_grid_power = max(0, min(remaining_grid_capacity, max_battery_power))  # Cap battery charging
                grid_setpoint = min(car_grid_need + battery_grid_power, max_grid_setpoint, max_grid_power)  # Multiple safety checks

                return {
                    "grid_setpoint": int(grid_setpoint),
                    "grid_setpoint_reason": f"Car {car_charging_power}W + battery {average_soc:.0f}% charging - grid for car ({int(car_grid_need)}W) + batteries ({int(battery_grid_power)}W) = {int(grid_setpoint)}W",
                }
            else:
                grid_setpoint = min(car_grid_need, max_grid_setpoint, max_grid_power)  # Safety check
                return {
                    "grid_setpoint": int(grid_setpoint),
                    "grid_setpoint_reason": f"Car drawing {car_charging_power}W, battery {average_soc:.0f}% ≥ {max_soc_threshold}% - grid covers car deficit ({int(grid_setpoint)}W)",
                }

        # Case 3: No significant car charging, but battery charging decision is on
        if not significant_car_charging and battery_grid_charging:
            # CRITICAL FIX: Add safety limits for battery-only charging
            max_battery_power = self.config.get(CONF_MAX_BATTERY_POWER, DEFAULT_MAX_BATTERY_POWER)
            max_grid_power = self.config.get(CONF_MAX_GRID_POWER, DEFAULT_MAX_GRID_POWER)
            grid_setpoint = min(max_grid_setpoint, max_battery_power, max_grid_power)  # Apply all safety limits
            return {
                "grid_setpoint": int(grid_setpoint),
                "grid_setpoint_reason": f"No car charging - grid setpoint for battery charging ({int(grid_setpoint)}W)",
            }

        # Case 4: No significant car charging, no battery charging
        return {
            "grid_setpoint": 0,
            "grid_setpoint_reason": "No significant car charging and no battery grid charging decision",
        }