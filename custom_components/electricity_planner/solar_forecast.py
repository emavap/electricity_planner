"""Solar forecast resolution with hourly-refreshing cache.

Extracted from ``coordinator.py`` as a standalone collaborator. Forecast
cache state (``_cached_solar_forecast`` / ``_solar_forecast_cache_date``
/ ``_solar_forecast_cache_hour`` / ``_solar_forecast_source``) remains on
the coordinator so existing tests and sensors that read those attributes
keep working.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.util import dt as dt_util

from .const import (
    CONF_SOLAR_FORECAST_ENTITY_TOMORROW,
    CONF_SOLAR_FORECAST_START_HOUR,
    CONF_SOLAR_FORECAST_TODAY_ENTITY,
    DEFAULT_SOLAR_FORECAST_START_HOUR,
)

if TYPE_CHECKING:
    from .coordinator import ElectricityPlannerCoordinator

_LOGGER = logging.getLogger(__name__)


class SolarForecastService:
    """Resolve ``energy_production_tomorrow`` with overnight cache handling."""

    def __init__(self, coordinator: ElectricityPlannerCoordinator) -> None:
        self._coordinator = coordinator

    async def resolve(self, entity_id: str | None = None) -> float | None:
        """Resolve the solar forecast value with hourly-refreshing cache.

        The forecast entity (``energy_production_tomorrow``) is read after the
        configured start hour (default 20:00) and cached hourly so that
        forecast improvements are picked up during the evening.

        After midnight the ``energy_production_tomorrow`` entity flips to the
        *next* day and no longer represents the day we're charging for.  If a
        "today" forecast entity is configured (``energy_production_today``), we
        use it live between midnight and the start hour because it now refers
        to the correct day.  Otherwise we fall back to the last cached value
        from the previous evening.
        """
        coordinator = self._coordinator
        now_local = dt_util.now()
        today = now_local.date()
        current_hour = now_local.hour
        start_hour = int(
            coordinator.config.get(CONF_SOLAR_FORECAST_START_HOUR, DEFAULT_SOLAR_FORECAST_START_HOUR)
        )
        tomorrow_entity = entity_id or coordinator.config.get(CONF_SOLAR_FORECAST_ENTITY_TOMORROW)
        today_entity = coordinator.config.get(CONF_SOLAR_FORECAST_TODAY_ENTITY)

        if current_hour >= start_hour:
            if not tomorrow_entity:
                coordinator._solar_forecast_source = "missing_tomorrow_entity"
                return None

            live_value = await coordinator._get_state_value(tomorrow_entity)
            # After start hour: refresh the cache once per hour (or if cache is empty).
            needs_refresh = (
                coordinator._solar_forecast_cache_date != today
                or coordinator._solar_forecast_cache_hour != current_hour
                or coordinator._cached_solar_forecast is None
            )
            stale_day_cache = (
                coordinator._solar_forecast_cache_date is not None
                and coordinator._solar_forecast_cache_date != today
            )
            if needs_refresh and live_value is not None:
                coordinator._cached_solar_forecast = live_value
                coordinator._solar_forecast_cache_date = today
                coordinator._solar_forecast_cache_hour = current_hour
                coordinator._solar_forecast_source = "tomorrow_live"
                _LOGGER.debug(
                    "Solar forecast cache updated for %s %02d:00: %.1f kWh",
                    today,
                    current_hour,
                    live_value,
                )
            elif needs_refresh and live_value is None and stale_day_cache:
                _LOGGER.warning(
                    "Solar forecast unavailable after start hour (%02d:00), "
                    "and cached value is from %s (stale day). Ignoring stale "
                    "cache until fresh forecast is available.",
                    start_hour,
                    coordinator._solar_forecast_cache_date,
                )
                coordinator._cached_solar_forecast = None
                coordinator._solar_forecast_cache_date = today
                coordinator._solar_forecast_cache_hour = current_hour
                coordinator._solar_forecast_source = "missing_live_after_start"
                return None
            if coordinator._cached_solar_forecast is not None:
                if live_value is None:
                    coordinator._solar_forecast_source = "tomorrow_cache"
                return coordinator._cached_solar_forecast
            coordinator._solar_forecast_source = "missing_live_after_start"
            return live_value

        # Before start hour: prefer the "today" entity (it now represents
        # the correct day after midnight) over the stale cache.
        if today_entity:
            today_value = await coordinator._get_state_value(today_entity)
            if today_value is not None:
                coordinator._solar_forecast_source = "today_live"
                _LOGGER.debug(
                    "Using energy_production_today (%.1f kWh) for "
                    "overnight forecast validation",
                    today_value,
                )
                return today_value

        # No "today" entity configured/available → use cached value
        if coordinator._cached_solar_forecast is not None:
            coordinator._solar_forecast_source = "overnight_cache"
            return coordinator._cached_solar_forecast
        # No cache yet (first run / restart before start hour).
        # The live "tomorrow" entity now points to the *wrong* day
        # (it flipped at midnight), so using it would give incorrect
        # sunny-day decisions.  Return None to disable the feature
        # until the start hour when the cache can be populated.
        live_value = await coordinator._get_state_value(tomorrow_entity) if tomorrow_entity else None
        if live_value is not None:
            reason = (
                "'today' entity unavailable" if today_entity
                else "no 'today' entity configured"
            )
            _LOGGER.warning(
                "Solar forecast: no cache and %s. "
                "Before the start hour (%02d:00) the 'tomorrow' entity "
                "points to the wrong day — sunny day logic disabled until "
                "cache is populated. Configure 'Energy Production Today' "
                "entity to avoid this gap.",
                reason,
                start_hour,
            )
        coordinator._solar_forecast_source = "none_before_start"
        return None
