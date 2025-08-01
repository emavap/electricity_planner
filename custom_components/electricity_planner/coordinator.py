"""Data coordinator for Electricity Planner."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN

from .const import (
    DOMAIN,
    CONF_ELECTRICITY_PRICE_ENTITY,
    CONF_BATTERY_SOC_ENTITIES,
    CONF_BATTERY_CAPACITY_ENTITIES,
    CONF_SOLAR_FORECAST_ENTITY,
    CONF_SOLAR_PRODUCTION_ENTITY,
    CONF_CAR_CHARGER_ENTITY,
    CONF_GRID_POWER_ENTITY,
    CONF_MIN_SOC_THRESHOLD,
    CONF_MAX_SOC_THRESHOLD,
    CONF_PRICE_THRESHOLD,
    CONF_SOLAR_FORECAST_HOURS,
    CONF_CAR_CHARGING_HOURS,
    DEFAULT_MIN_SOC,
    DEFAULT_MAX_SOC,
    DEFAULT_PRICE_THRESHOLD,
    DEFAULT_SOLAR_FORECAST_HOURS,
    DEFAULT_CAR_CHARGING_HOURS,
)
from .decision_engine import ChargingDecisionEngine

_LOGGER = logging.getLogger(__name__)


class ElectricityPlannerCoordinator(DataUpdateCoordinator):
    """Coordinator for electricity planner data."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.hass = hass
        self.entry = entry
        self.config = entry.data
        
        self.decision_engine = ChargingDecisionEngine(hass, self.config)
        
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=5),
        )

        self._setup_entity_listeners()

    def _setup_entity_listeners(self):
        """Set up listeners for entity state changes."""
        entities_to_track = []
        
        if self.config.get(CONF_ELECTRICITY_PRICE_ENTITY):
            entities_to_track.append(self.config[CONF_ELECTRICITY_PRICE_ENTITY])
        
        if self.config.get(CONF_BATTERY_SOC_ENTITIES):
            entities_to_track.extend(self.config[CONF_BATTERY_SOC_ENTITIES])
        
        if self.config.get(CONF_SOLAR_PRODUCTION_ENTITY):
            entities_to_track.append(self.config[CONF_SOLAR_PRODUCTION_ENTITY])
        
        if self.config.get(CONF_GRID_POWER_ENTITY):
            entities_to_track.append(self.config[CONF_GRID_POWER_ENTITY])

        if entities_to_track:
            async_track_state_change_event(
                self.hass, entities_to_track, self._handle_entity_change
            )

    @callback
    def _handle_entity_change(self, event):
        """Handle entity state changes."""
        _LOGGER.debug("Entity changed: %s", event.data.get("entity_id"))
        self.async_set_updated_data(self.data)

    async def _async_update_data(self) -> dict[str, Any]:
        """Update data via library."""
        try:
            data = await self._fetch_all_data()
            
            charging_decision = await self.decision_engine.evaluate_charging_decision(data)
            
            data.update(charging_decision)
            
            return data
            
        except Exception as err:
            raise UpdateFailed(f"Error communicating with entities: {err}") from err

    async def _fetch_all_data(self) -> dict[str, Any]:
        """Fetch data from all configured entities."""
        data = {}
        
        electricity_price = await self._get_state_value(
            self.config.get(CONF_ELECTRICITY_PRICE_ENTITY)
        )
        data["electricity_price"] = electricity_price
        
        battery_soc_entities = self.config.get(CONF_BATTERY_SOC_ENTITIES, [])
        battery_soc_values = []
        for entity_id in battery_soc_entities:
            soc = await self._get_state_value(entity_id)
            if soc is not None:
                battery_soc_values.append({"entity_id": entity_id, "soc": soc})
        data["battery_soc"] = battery_soc_values
        
        battery_capacity_entities = self.config.get(CONF_BATTERY_CAPACITY_ENTITIES, [])
        battery_capacity_values = []
        for entity_id in battery_capacity_entities:
            capacity = await self._get_state_value(entity_id)
            if capacity is not None:
                battery_capacity_values.append({"entity_id": entity_id, "capacity": capacity})
        data["battery_capacity"] = battery_capacity_values
        
        solar_forecast = await self._get_state_value(
            self.config.get(CONF_SOLAR_FORECAST_ENTITY)
        )
        data["solar_forecast"] = solar_forecast
        
        solar_production = await self._get_state_value(
            self.config.get(CONF_SOLAR_PRODUCTION_ENTITY)
        )
        data["solar_production"] = solar_production
        
        grid_power = await self._get_state_value(
            self.config.get(CONF_GRID_POWER_ENTITY)
        )
        data["grid_power"] = grid_power
        
        return data

    async def _get_state_value(self, entity_id: str | None) -> float | None:
        """Get numeric state value from entity."""
        if not entity_id:
            return None
            
        state = self.hass.states.get(entity_id)
        if not state or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
            
        try:
            return float(state.state)
        except (ValueError, TypeError):
            _LOGGER.warning("Could not convert state to float: %s = %s", entity_id, state.state)
            return None

    async def set_car_charger_state(self, state: bool) -> bool:
        """Control car charger state."""
        car_charger_entity = self.config.get(CONF_CAR_CHARGER_ENTITY)
        if not car_charger_entity:
            _LOGGER.warning("No car charger entity configured")
            return False
        
        service = "switch.turn_on" if state else "switch.turn_off"
        try:
            await self.hass.services.async_call(
                "switch",
                "turn_on" if state else "turn_off",
                {"entity_id": car_charger_entity},
                blocking=True
            )
            _LOGGER.info("Car charger %s: %s", "enabled" if state else "disabled", car_charger_entity)
            return True
        except Exception as err:
            _LOGGER.error("Failed to control car charger %s: %s", car_charger_entity, err)
            return False

    @property
    def min_soc_threshold(self) -> float:
        """Get minimum SOC threshold."""
        return self.config.get(CONF_MIN_SOC_THRESHOLD, DEFAULT_MIN_SOC)

    @property
    def max_soc_threshold(self) -> float:
        """Get maximum SOC threshold."""
        return self.config.get(CONF_MAX_SOC_THRESHOLD, DEFAULT_MAX_SOC)

    @property
    def price_threshold(self) -> float:
        """Get price threshold."""
        return self.config.get(CONF_PRICE_THRESHOLD, DEFAULT_PRICE_THRESHOLD)

    @property
    def solar_forecast_hours(self) -> int:
        """Get solar forecast hours."""
        return self.config.get(CONF_SOLAR_FORECAST_HOURS, DEFAULT_SOLAR_FORECAST_HOURS)

    @property
    def car_charging_hours(self) -> int:
        """Get car charging hours."""
        return self.config.get(CONF_CAR_CHARGING_HOURS, DEFAULT_CAR_CHARGING_HOURS)