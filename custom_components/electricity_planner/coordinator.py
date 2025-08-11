"""Data coordinator for Electricity Planner."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN

from .const import (
    DOMAIN,
    CONF_CURRENT_PRICE_ENTITY,
    CONF_HIGHEST_PRICE_ENTITY,
    CONF_LOWEST_PRICE_ENTITY,
    CONF_NEXT_PRICE_ENTITY,
    CONF_BATTERY_SOC_ENTITIES,
    CONF_SOLAR_PRODUCTION_ENTITY,
    CONF_HOUSE_CONSUMPTION_ENTITY,
    CONF_CAR_CHARGING_POWER_ENTITY,
    CONF_MONTHLY_GRID_PEAK_ENTITY,
    CONF_SOLAR_FORECAST_CURRENT_ENTITY,
    CONF_SOLAR_FORECAST_NEXT_ENTITY,
    CONF_SOLAR_FORECAST_TODAY_ENTITY,
    CONF_SOLAR_FORECAST_REMAINING_TODAY_ENTITY,
    CONF_SOLAR_FORECAST_TOMORROW_ENTITY,
    CONF_MIN_SOC_THRESHOLD,
    CONF_MAX_SOC_THRESHOLD,
    CONF_PRICE_THRESHOLD,
    DEFAULT_MIN_SOC,
    DEFAULT_MAX_SOC,
    DEFAULT_PRICE_THRESHOLD,
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

        # Data availability tracking
        self._last_successful_update = datetime.now()
        self._data_unavailable_since = None
        self._notification_sent = False

        # Update throttling
        self._last_entity_update = None
        self._min_update_interval = timedelta(seconds=10)  # Minimum 10s between entity-triggered updates

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=30),  # Maximum 30s updates (minimum 10s via entity changes)
        )
        
        self._setup_entity_listeners()

    def _is_data_available(self, data: dict[str, Any]) -> bool:
        """Check if critical data is available for decisions."""
        price_available = data.get("current_price") is not None
        price_analysis_available = data.get("price_analysis", {}).get("data_available", False)
        return price_available and price_analysis_available

    def _setup_entity_listeners(self):
        """Set up listeners for entity state changes."""
        entities_to_track = []

        # Price entities
        for entity_key in [CONF_CURRENT_PRICE_ENTITY, CONF_HIGHEST_PRICE_ENTITY,
                          CONF_LOWEST_PRICE_ENTITY, CONF_NEXT_PRICE_ENTITY]:
            if self.config.get(entity_key):
                entities_to_track.append(self.config[entity_key])

        # Battery entities
        if self.config.get(CONF_BATTERY_SOC_ENTITIES):
            entities_to_track.extend(self.config[CONF_BATTERY_SOC_ENTITIES])

        # Power entities
        for entity_key in [CONF_SOLAR_PRODUCTION_ENTITY, CONF_HOUSE_CONSUMPTION_ENTITY, CONF_CAR_CHARGING_POWER_ENTITY, CONF_MONTHLY_GRID_PEAK_ENTITY]:
            if self.config.get(entity_key):
                entities_to_track.append(self.config[entity_key])

        # Solar forecast entities
        for entity_key in [CONF_SOLAR_FORECAST_CURRENT_ENTITY, CONF_SOLAR_FORECAST_NEXT_ENTITY,
                          CONF_SOLAR_FORECAST_TODAY_ENTITY, CONF_SOLAR_FORECAST_REMAINING_TODAY_ENTITY,
                          CONF_SOLAR_FORECAST_TOMORROW_ENTITY]:
            if self.config.get(entity_key):
                entities_to_track.append(self.config[entity_key])

        if entities_to_track:
            async_track_state_change_event(
                self.hass, entities_to_track, self._handle_entity_change
            )

    @callback
    def _handle_entity_change(self, event):
        """Handle entity state changes with minimum update interval."""
        entity_id = event.data.get("entity_id")
        _LOGGER.debug("Entity changed: %s", entity_id)

        # Trigger immediate updates for critical entities
        critical_entities = [
            self.config.get(CONF_CURRENT_PRICE_ENTITY),
            self.config.get(CONF_SOLAR_PRODUCTION_ENTITY),
            self.config.get(CONF_HOUSE_CONSUMPTION_ENTITY),
        ]

        # Trigger updates for battery SOC changes (any configured battery)
        battery_entities = self.config.get(CONF_BATTERY_SOC_ENTITIES, [])

        if entity_id in critical_entities or entity_id in battery_entities:
            now = datetime.now()

            # Apply minimum interval throttling
            if (self._last_entity_update is None or
                now - self._last_entity_update >= self._min_update_interval):

                self._last_entity_update = now
                self.async_create_task(self.async_request_refresh())
                _LOGGER.debug("Entity update triggered for %s (throttled to %ds minimum)",
                            entity_id, self._min_update_interval.total_seconds())
            else:
                time_remaining = (self._last_entity_update + self._min_update_interval - now).total_seconds()
                _LOGGER.debug("Entity update skipped for %s (throttled, %.1fs remaining)",
                            entity_id, time_remaining)

    async def _async_update_data(self) -> dict[str, Any]:
        """Update data via library."""
        try:
            data = await self._fetch_all_data()

            charging_decision = await self.decision_engine.evaluate_charging_decision(data)

            data.update(charging_decision)

            # Check data availability and handle notifications
            await self._check_data_availability(data)

            return data

        except Exception as err:
            raise UpdateFailed(f"Error communicating with entities: {err}") from err

    async def _fetch_all_data(self) -> dict[str, Any]:
        """Fetch data from all configured entities."""
        data = {}

        # Price data
        data["current_price"] = await self._get_state_value(
            self.config.get(CONF_CURRENT_PRICE_ENTITY)
        )
        data["highest_price"] = await self._get_state_value(
            self.config.get(CONF_HIGHEST_PRICE_ENTITY)
        )
        data["lowest_price"] = await self._get_state_value(
            self.config.get(CONF_LOWEST_PRICE_ENTITY)
        )
        data["next_price"] = await self._get_state_value(
            self.config.get(CONF_NEXT_PRICE_ENTITY)
        )

        # Battery SOC data
        battery_soc_entities = self.config.get(CONF_BATTERY_SOC_ENTITIES, [])
        battery_soc_values = []

        _LOGGER.debug("Battery SOC entities configured: %s", battery_soc_entities)

        for entity_id in battery_soc_entities:
            soc = await self._get_state_value(entity_id)
            state = self.hass.states.get(entity_id)
            _LOGGER.debug("Battery entity %s: state=%s, parsed_value=%s",
                         entity_id, state.state if state else "missing", soc)
            if soc is not None:
                battery_soc_values.append({"entity_id": entity_id, "soc": soc})
            else:
                _LOGGER.warning("Battery entity %s is unavailable - excluding from calculations", entity_id)

        data["battery_soc"] = battery_soc_values
        _LOGGER.debug("Final battery SOC data: %s", battery_soc_values)

        # Power data
        solar_production = await self._get_state_value(
            self.config.get(CONF_SOLAR_PRODUCTION_ENTITY)
        )
        house_consumption = await self._get_state_value(
            self.config.get(CONF_HOUSE_CONSUMPTION_ENTITY)
        )
        
        data["solar_production"] = solar_production
        data["house_consumption"] = house_consumption
        
        # Calculate solar surplus dynamically (production - consumption, cannot be negative)
        if solar_production is not None and house_consumption is not None:
            solar_surplus = max(0, solar_production - house_consumption)
        elif solar_production is not None:
            solar_surplus = solar_production  # Assume minimal consumption if not available
        else:
            solar_surplus = 0  # No solar data available
            
        data["solar_surplus"] = solar_surplus

        _LOGGER.debug("Solar production: %sW, house consumption: %sW, available surplus: %sW (for batteries/car/export)", 
                     solar_production, house_consumption, solar_surplus)

        data["car_charging_power"] = await self._get_state_value(
            self.config.get(CONF_CAR_CHARGING_POWER_ENTITY)
        )

        data["monthly_grid_peak"] = await self._get_state_value(
            self.config.get(CONF_MONTHLY_GRID_PEAK_ENTITY)
        )


        # Solar forecast data
        data["solar_forecast_current"] = await self._get_state_value(
            self.config.get(CONF_SOLAR_FORECAST_CURRENT_ENTITY)
        )
        data["solar_forecast_next"] = await self._get_state_value(
            self.config.get(CONF_SOLAR_FORECAST_NEXT_ENTITY)  
        )
        data["solar_forecast_today"] = await self._get_state_value(
            self.config.get(CONF_SOLAR_FORECAST_TODAY_ENTITY)
        )
        data["solar_forecast_remaining_today"] = await self._get_state_value(
            self.config.get(CONF_SOLAR_FORECAST_REMAINING_TODAY_ENTITY)
        )
        data["solar_forecast_tomorrow"] = await self._get_state_value(
            self.config.get(CONF_SOLAR_FORECAST_TOMORROW_ENTITY)
        )

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

    async def _check_data_availability(self, data: dict[str, Any]) -> None:
        """Check data availability and send notifications if needed."""
        now = datetime.now()

        # Check if critical data is available
        data_is_available = self._is_data_available(data)

        if data_is_available:
            # Data is available - reset tracking
            self._last_successful_update = now
            if self._data_unavailable_since is not None:
                # Data was unavailable but is now available - send recovery notification
                unavailable_duration = now - self._data_unavailable_since
                await self._send_notification(
                    "Electricity Planner Data Restored",
                    f"Nord Pool data has been restored after {unavailable_duration.total_seconds():.0f} seconds. "
                    f"Charging decisions are now active.",
                    "electricity_planner_data_restored"
                )
                self._data_unavailable_since = None
                self._notification_sent = False
                _LOGGER.info("Data availability restored after %.1f seconds", unavailable_duration.total_seconds())
        else:
            # Data is not available
            if self._data_unavailable_since is None:
                # First time detecting unavailability
                self._data_unavailable_since = now
                _LOGGER.warning("Critical data unavailable - starting tracking")
            else:
                # Data has been unavailable for some time
                unavailable_duration = now - self._data_unavailable_since

                # Send notification if data unavailable for more than 1 minute and notification not sent yet
                if unavailable_duration > timedelta(minutes=1) and not self._notification_sent:
                    await self._send_notification(
                        "Electricity Planner Data Unavailable",
                        f"Critical data (Nord Pool prices) has been unavailable for {unavailable_duration.total_seconds():.0f} seconds. "
                        f"All charging from grid is disabled for safety. Please check your Nord Pool integration.",
                        "electricity_planner_data_unavailable"
                    )
                    self._notification_sent = True
                    _LOGGER.error("Data unavailable notification sent after %.1f seconds", unavailable_duration.total_seconds())

    async def _send_notification(self, title: str, message: str, notification_id: str) -> None:
        """Send a persistent notification."""
        try:
            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": title,
                    "message": message,
                    "notification_id": notification_id,
                },
            )
            _LOGGER.info("Sent notification: %s", title)
        except Exception as err:
            _LOGGER.error("Failed to send notification: %s", err)

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

