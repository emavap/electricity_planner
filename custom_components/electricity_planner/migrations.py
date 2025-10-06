"""Migration utilities for Electricity Planner configuration."""
from __future__ import annotations

import logging
from typing import Any, Dict

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_GRID_BATTERY_CHARGING_LIMIT_SOC,
    CONF_BASE_GRID_SETPOINT,
    DEFAULT_GRID_BATTERY_CHARGING_LIMIT_SOC,
    DEFAULT_BASE_GRID_SETPOINT,
)

_LOGGER = logging.getLogger(__name__)

# Current config version
CURRENT_VERSION = 2


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate configuration entry to latest version."""
    _LOGGER.info("Migrating configuration from version %s", entry.version)

    if entry.version == 1:
        # Migrate from version 1 to version 2
        new_data = {**entry.data}
        
        # Add new configuration options introduced in v2
        if CONF_GRID_BATTERY_CHARGING_LIMIT_SOC not in new_data:
            new_data[CONF_GRID_BATTERY_CHARGING_LIMIT_SOC] = DEFAULT_GRID_BATTERY_CHARGING_LIMIT_SOC
            _LOGGER.info("Added grid battery charging limit SOC: %s%%", 
                        DEFAULT_GRID_BATTERY_CHARGING_LIMIT_SOC)
        
        if CONF_BASE_GRID_SETPOINT not in new_data:
            new_data[CONF_BASE_GRID_SETPOINT] = DEFAULT_BASE_GRID_SETPOINT
            _LOGGER.info("Added base grid setpoint: %sW", DEFAULT_BASE_GRID_SETPOINT)
        
        # Update entry with new data
        hass.config_entries.async_update_entry(
            entry,
            data=new_data,
            version=2
        )
        
        _LOGGER.info("Migration to version 2 complete")
    
    # Future migrations would go here
    # elif entry.version == 2:
    #     # Migrate from version 2 to version 3
    #     ...
    
    return True


def migrate_config_data(old_data: Dict[str, Any], from_version: int) -> Dict[str, Any]:
    """Migrate configuration data structure (for testing)."""
    new_data = {**old_data}
    
    if from_version < 2:
        # Add v2 fields if not present
        if CONF_GRID_BATTERY_CHARGING_LIMIT_SOC not in new_data:
            new_data[CONF_GRID_BATTERY_CHARGING_LIMIT_SOC] = DEFAULT_GRID_BATTERY_CHARGING_LIMIT_SOC
        
        if CONF_BASE_GRID_SETPOINT not in new_data:
            new_data[CONF_BASE_GRID_SETPOINT] = DEFAULT_BASE_GRID_SETPOINT
    
    return new_data


class ConfigVersionManager:
    """Manage configuration versions and compatibility."""
    
    @staticmethod
    def get_version(config: Dict[str, Any]) -> int:
        """Get configuration version."""
        return config.get("_version", 1)
    
    @staticmethod
    def set_version(config: Dict[str, Any], version: int) -> Dict[str, Any]:
        """Set configuration version."""
        new_config = {**config}
        new_config["_version"] = version
        return new_config
    
    @staticmethod
    def is_compatible(config: Dict[str, Any]) -> bool:
        """Check if configuration is compatible with current version."""
        config_version = ConfigVersionManager.get_version(config)
        return config_version <= CURRENT_VERSION
    
    @staticmethod
    def needs_migration(config: Dict[str, Any]) -> bool:
        """Check if configuration needs migration."""
        config_version = ConfigVersionManager.get_version(config)
        return config_version < CURRENT_VERSION
