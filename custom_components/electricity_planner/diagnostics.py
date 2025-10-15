"""Diagnostics helpers for Electricity Planner."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_EXPORT_KEYS = [
    "battery_grid_charging",
    "battery_grid_charging_reason",
    "car_grid_charging",
    "car_grid_charging_reason",
    "manual_overrides",
    "strategy_trace",
    "price_analysis",
    "battery_analysis",
    "power_analysis",
    "power_allocation",
    "solar_analysis",
    "time_context",
    "has_min_charging_window",
    "average_threshold",
    "transport_cost_status",
]


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a given config entry."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is None:
        return {"error": "coordinator_unavailable"}

    data = coordinator.data or {}
    diagnostics = {_key: deepcopy(data.get(_key)) for _key in _EXPORT_KEYS if _key in data}

    return {
        "config_entry": {
            "entry_id": entry.entry_id,
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "coordinator_meta": {
            "last_successful_update": getattr(coordinator, "last_successful_update", None),
            "data_unavailable_since": getattr(coordinator, "data_unavailable_since", None),
            "notification_sent": getattr(coordinator, "notification_sent", False),
        },
        "diagnostics": diagnostics,
    }
