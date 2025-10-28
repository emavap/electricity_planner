"""The Electricity Planner integration."""
from __future__ import annotations

import logging
from datetime import timedelta

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import (
    ATTR_ACTION,
    ATTR_DURATION,
    ATTR_ENTRY_ID,
    ATTR_REASON,
    ATTR_TARGET,
    DOMAIN,
    MANUAL_OVERRIDE_ACTION_FORCE_CHARGE,
    MANUAL_OVERRIDE_ACTION_FORCE_WAIT,
    MANUAL_OVERRIDE_TARGET_BATTERY,
    MANUAL_OVERRIDE_TARGET_BOTH,
    MANUAL_OVERRIDE_TARGET_CAR,
    SERVICE_CLEAR_MANUAL_OVERRIDE,
    SERVICE_SET_MANUAL_OVERRIDE,
)
from .coordinator import ElectricityPlannerCoordinator
from .dashboard import async_remove_dashboard, async_setup_or_update_dashboard
from .migrations import async_migrate_entry

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.SWITCH]

MANUAL_OVERRIDE_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY_ID): str,
        vol.Required(ATTR_TARGET): vol.In(
            (
                MANUAL_OVERRIDE_TARGET_BATTERY,
                MANUAL_OVERRIDE_TARGET_CAR,
                MANUAL_OVERRIDE_TARGET_BOTH,
            )
        ),
        vol.Required(ATTR_ACTION): vol.In(
            (MANUAL_OVERRIDE_ACTION_FORCE_CHARGE, MANUAL_OVERRIDE_ACTION_FORCE_WAIT)
        ),
        vol.Optional(ATTR_DURATION): vol.All(vol.Coerce(int), vol.Range(min=1, max=1440)),
        vol.Optional(ATTR_REASON): vol.Coerce(str),
    }
)

CLEAR_OVERRIDE_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY_ID): str,
        vol.Optional(ATTR_TARGET, default=MANUAL_OVERRIDE_TARGET_BOTH): vol.In(
            (
                MANUAL_OVERRIDE_TARGET_BATTERY,
                MANUAL_OVERRIDE_TARGET_CAR,
                MANUAL_OVERRIDE_TARGET_BOTH,
            )
        ),
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Electricity Planner from a config entry."""
    # Perform migration if needed
    if entry.version < 10:
        await async_migrate_entry(hass, entry)
    
    coordinator = ElectricityPlannerCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    _register_services_once(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    hass.async_create_task(async_setup_or_update_dashboard(hass, entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
        await async_remove_dashboard(hass, entry)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle an options update."""
    await hass.config_entries.async_reload(entry.entry_id)


def _register_services_once(hass: HomeAssistant) -> None:
    """Register domain services if not already registered."""
    registry = hass.data.setdefault(DOMAIN, {})
    if registry.get("services_registered"):
        return

    def _coordinator_entries() -> dict[str, ElectricityPlannerCoordinator]:
        """Return mapping of entry_id to coordinator, ignoring auxiliary keys."""
        return {
            entry_id: coordinator
            for entry_id, coordinator in hass.data.get(DOMAIN, {}).items()
            if isinstance(coordinator, ElectricityPlannerCoordinator)
        }

    def _resolve_entry_id(provided_id: str | None) -> str:
        """Resolve which config entry to target for a service call."""
        coordinators = _coordinator_entries()

        if provided_id:
            if provided_id in coordinators:
                return provided_id
            raise HomeAssistantError(f"No Electricity Planner config entry with id {provided_id}")

        if not coordinators:
            raise HomeAssistantError("No Electricity Planner config entries loaded.")

        if len(coordinators) == 1:
            return next(iter(coordinators))

        raise HomeAssistantError(
            "Multiple Electricity Planner entries configured; specify entry_id."
        )

    async def _async_handle_set_override(call):
        entry_id = _resolve_entry_id(call.data.get(ATTR_ENTRY_ID))
        coordinator: ElectricityPlannerCoordinator | None = hass.data.get(DOMAIN, {}).get(entry_id)

        if not coordinator:
            raise HomeAssistantError(f"Coordinator for entry {entry_id} is no longer available")

        target = call.data[ATTR_TARGET]
        action = call.data[ATTR_ACTION]
        reason = call.data.get(ATTR_REASON)
        duration_minutes = call.data.get(ATTR_DURATION)
        duration = timedelta(minutes=duration_minutes) if duration_minutes is not None else None

        value = action == MANUAL_OVERRIDE_ACTION_FORCE_CHARGE

        await coordinator.async_set_manual_override(target, value, duration, reason)
        await coordinator.async_request_refresh()

    async def _async_handle_clear_override(call):
        entry_id = _resolve_entry_id(call.data.get(ATTR_ENTRY_ID))
        coordinator: ElectricityPlannerCoordinator | None = hass.data.get(DOMAIN, {}).get(entry_id)

        if not coordinator:
            raise HomeAssistantError(f"Coordinator for entry {entry_id} is no longer available")

        target = call.data.get(ATTR_TARGET, MANUAL_OVERRIDE_TARGET_BOTH)
        await coordinator.async_clear_manual_override(target)
        await coordinator.async_request_refresh()

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_MANUAL_OVERRIDE,
        _async_handle_set_override,
        schema=MANUAL_OVERRIDE_SERVICE_SCHEMA,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_CLEAR_MANUAL_OVERRIDE,
        _async_handle_clear_override,
        schema=CLEAR_OVERRIDE_SERVICE_SCHEMA,
    )

    registry["services_registered"] = True
