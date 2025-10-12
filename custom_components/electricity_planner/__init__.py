"""The Electricity Planner integration."""
from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.frontend import (
    async_register_built_in_panel,
    async_remove_panel,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import ElectricityPlannerCoordinator
from .migrations import async_migrate_entry

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR]

PANEL_URL_PATH = "electricity-planner"
PANEL_DATA_FLAG = "panel_registered"
PANEL_FILE = str((Path(__file__).resolve().parent.parent / "electricity_planner_dashboard.yaml").resolve())


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Electricity Planner from a config entry."""
    # Perform migration if needed
    if entry.version < 4:
        await async_migrate_entry(hass, entry)
    
    coordinator = ElectricityPlannerCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await _ensure_dashboard_panel(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data[DOMAIN]:
            await _remove_dashboard_panel(hass)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle an options update."""
    await hass.config_entries.async_reload(entry.entry_id)


async def _ensure_dashboard_panel(hass: HomeAssistant) -> None:
    """Register the built-in dashboard panel if needed."""
    domain_data = hass.data.setdefault(DOMAIN, {})

    if domain_data.get(PANEL_DATA_FLAG):
        return

    # If the panel already exists (e.g. from a previous restart), mark it as registered
    if PANEL_URL_PATH in hass.data.get("frontend_panels", {}):
        domain_data[PANEL_DATA_FLAG] = True
        return

    async_register_built_in_panel(
        hass,
        component_name="lovelace",
        sidebar_title="Electricity Planner",
        sidebar_icon="mdi:lightning-bolt",
        url_path=PANEL_URL_PATH,
        config={"mode": "yaml", "filename": PANEL_FILE},
        require_admin=False,
    )

    domain_data[PANEL_DATA_FLAG] = True


async def _remove_dashboard_panel(hass: HomeAssistant) -> None:
    """Unregister the custom dashboard panel when no entries remain."""
    if PANEL_URL_PATH in hass.data.get("frontend_panels", {}):
        async_remove_panel(hass, PANEL_URL_PATH)

    # Ensure we drop the registration flag if domain storage remains
    domain_data = hass.data.setdefault(DOMAIN, {})
    domain_data.pop(PANEL_DATA_FLAG, None)
