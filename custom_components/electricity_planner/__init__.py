"""The Electricity Planner integration."""
from __future__ import annotations

import logging
from datetime import timedelta

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from .const import (
    ATTR_ACTION,
    ATTR_CHARGER_LIMIT_OVERRIDE,
    ATTR_DURATION,
    ATTR_ENTRY_ID,
    ATTR_GRID_SETPOINT_OVERRIDE,
    ATTR_REASON,
    ATTR_TARGET,
    CONF_ARBITRAGE_MODE_DEADLINE_HOUR,
    CONF_ARBITRAGE_MODE_RESERVE_SOC,
    CONF_MAX_SOC_THRESHOLD,
    CONF_MAX_SOC_THRESHOLD_SUNNY,
    CONF_SOLAR_FORECAST_START_HOUR,
    CONF_SUNNY_FORECAST_THRESHOLD_KWH,
    DOMAIN,
    MANUAL_OVERRIDE_ACTION_FORCE_CHARGE,
    MANUAL_OVERRIDE_ACTION_FORCE_WAIT,
    MANUAL_OVERRIDE_TARGET_ALL,
    MANUAL_OVERRIDE_TARGET_ARBITRAGE_MODE,
    MANUAL_OVERRIDE_TARGET_BATTERY,
    MANUAL_OVERRIDE_TARGET_BOTH,
    MANUAL_OVERRIDE_TARGET_CAR,
    MANUAL_OVERRIDE_TARGET_CHARGER_LIMIT,
    MANUAL_OVERRIDE_TARGET_GRID_SETPOINT,
    MANUAL_OVERRIDE_TARGET_NEGATIVE_BUY,
    SERVICE_CLEAR_MANUAL_OVERRIDE,
    SERVICE_SET_MANUAL_OVERRIDE,
)
from .coordinator import ElectricityPlannerCoordinator
from .dashboard import async_remove_dashboard, async_setup_or_update_dashboard
from .migrations import CURRENT_VERSION, async_migrate_entry

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.SWITCH, Platform.NUMBER]

NUMBER_ENTITY_ID_SUFFIXES: tuple[tuple[str, str], ...] = (
    # First column is the unique_id suffix (preserved across the v22→v23 rename
    # so historical statistics survive); second column is the desired entity_id
    # slug.  The migration helper looks up the current entity by unique_id and
    # only updates the entity_id, so the legacy ``battery_dump_target_soc``
    # unique_id continues to identify the arbitrage-reserve number.
    ("battery_dump_target_soc", "arbitrage_mode_reserve_soc"),
    ("arbitrage_mode_deadline_hour", "arbitrage_mode_deadline_hour"),
    ("max_soc_threshold", "max_soc_threshold"),
    ("max_soc_threshold_sunny", "max_soc_threshold_sunny"),
    ("max_soc_threshold_solar", "max_soc_threshold_solar"),
    ("sunny_forecast_threshold_kwh", "sunny_forecast_threshold_kwh"),
)

MANUAL_OVERRIDE_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY_ID): str,
        vol.Required(ATTR_TARGET): vol.In(
            (
                MANUAL_OVERRIDE_TARGET_BATTERY,
                MANUAL_OVERRIDE_TARGET_ARBITRAGE_MODE,
                MANUAL_OVERRIDE_TARGET_NEGATIVE_BUY,
                MANUAL_OVERRIDE_TARGET_CAR,
                MANUAL_OVERRIDE_TARGET_BOTH,
                MANUAL_OVERRIDE_TARGET_CHARGER_LIMIT,
                MANUAL_OVERRIDE_TARGET_GRID_SETPOINT,
            )
        ),
        vol.Optional(ATTR_ACTION): vol.In(
            (MANUAL_OVERRIDE_ACTION_FORCE_CHARGE, MANUAL_OVERRIDE_ACTION_FORCE_WAIT)
        ),
        vol.Optional(ATTR_DURATION): vol.All(vol.Coerce(int), vol.Range(min=1, max=1440)),
        vol.Optional(ATTR_REASON): vol.Coerce(str),
        vol.Optional(ATTR_CHARGER_LIMIT_OVERRIDE): vol.All(vol.Coerce(int), vol.Range(min=0, max=50000)),
        vol.Optional(ATTR_GRID_SETPOINT_OVERRIDE): vol.All(vol.Coerce(int), vol.Range(min=-50000, max=50000)),
    }
)

CLEAR_OVERRIDE_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY_ID): str,
        vol.Optional(ATTR_TARGET, default=MANUAL_OVERRIDE_TARGET_ALL): vol.In(
            (
                MANUAL_OVERRIDE_TARGET_BATTERY,
                MANUAL_OVERRIDE_TARGET_ARBITRAGE_MODE,
                MANUAL_OVERRIDE_TARGET_NEGATIVE_BUY,
                MANUAL_OVERRIDE_TARGET_CAR,
                MANUAL_OVERRIDE_TARGET_BOTH,
                MANUAL_OVERRIDE_TARGET_CHARGER_LIMIT,
                MANUAL_OVERRIDE_TARGET_GRID_SETPOINT,
                MANUAL_OVERRIDE_TARGET_ALL,
            )
        ),
    }
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Electricity Planner from a config entry."""
    # Perform migration if needed
    if entry.version < CURRENT_VERSION:
        await async_migrate_entry(hass, entry)

    coordinator = ElectricityPlannerCoordinator(hass, entry)
    await coordinator.async_initialize_persistent_state()
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = coordinator

    _register_services_once(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await _async_migrate_number_entity_ids(hass, entry)
    await _async_migrate_switch_entity_ids(hass, entry)
    hass.async_create_task(async_setup_or_update_dashboard(hass, entry))

    # Register listener for options updates to trigger reload
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        coordinator = hass.data[DOMAIN].pop(entry.entry_id)
        # Unsubscribe from entity state change tracking
        if hasattr(coordinator, "_entity_unsub") and coordinator._entity_unsub:
            coordinator._entity_unsub()
        await async_remove_dashboard(hass, entry)

    return unload_ok


async def _async_migrate_number_entity_ids(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Rename number entities to stable IDs used by dashboard templates."""
    await _async_migrate_entity_ids(hass, entry, "number", NUMBER_ENTITY_ID_SUFFIXES)


async def _async_migrate_switch_entity_ids(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Rename arbitrage switch entity IDs and unique IDs to the new internal key."""
    registry = er.async_get(hass)
    title_slug = slugify(entry.title or "electricity_planner")
    old_unique_id = f"{entry.entry_id}_battery_dump_to_grid"
    new_unique_id = f"{entry.entry_id}_arbitrage_mode"
    current_entity_id = registry.async_get_entity_id("switch", DOMAIN, old_unique_id)
    if not current_entity_id:
        return

    target_entity_id = f"switch.{title_slug}_arbitrage_mode"
    existing_new_entry_id = registry.async_get_entity_id("switch", DOMAIN, new_unique_id)
    if existing_new_entry_id and existing_new_entry_id != current_entity_id:
        _LOGGER.warning(
            "Skipping switch unique ID migration for %s because %s already exists",
            current_entity_id,
            existing_new_entry_id,
        )
        return

    update_kwargs: dict[str, str] = {"new_unique_id": new_unique_id}
    if current_entity_id != target_entity_id:
        if registry.async_get(target_entity_id) is not None:
            _LOGGER.warning(
                "Skipping switch entity ID migration for %s -> %s because target already exists",
                current_entity_id,
                target_entity_id,
            )
        else:
            update_kwargs["new_entity_id"] = target_entity_id

    try:
        registry.async_update_entity(current_entity_id, **update_kwargs)
        _LOGGER.info(
            "Migrated switch registry entry for %s: %s -> %s",
            old_unique_id,
            current_entity_id,
            update_kwargs.get("new_entity_id", current_entity_id),
        )
    except ValueError as err:
        _LOGGER.warning(
            "Failed to migrate switch registry entry for %s: %s (%s)",
            old_unique_id,
            current_entity_id,
            err,
        )


async def _async_migrate_entity_ids(
    hass: HomeAssistant,
    entry: ConfigEntry,
    platform: str,
    migrations: tuple[tuple[str, str], ...],
) -> None:
    """Rename entities to stable IDs used by dashboard templates."""
    registry = er.async_get(hass)
    title_slug = slugify(entry.title or "electricity_planner")

    for unique_suffix, object_suffix in migrations:
        unique_id = f"{entry.entry_id}_{unique_suffix}"
        current_entity_id = registry.async_get_entity_id(platform, DOMAIN, unique_id)
        if not current_entity_id:
            continue

        target_entity_id = f"{platform}.{title_slug}_{object_suffix}"
        if current_entity_id == target_entity_id:
            continue

        if registry.async_get(target_entity_id) is not None:
            _LOGGER.warning(
                "Skipping entity ID migration for %s -> %s because target already exists",
                current_entity_id,
                target_entity_id,
            )
            continue

        try:
            registry.async_update_entity(
                current_entity_id,
                new_entity_id=target_entity_id,
            )
            _LOGGER.info(
                "Migrated %s entity ID for %s: %s -> %s",
                platform,
                unique_id,
                current_entity_id,
                target_entity_id,
            )
        except ValueError as err:
            _LOGGER.warning(
                "Failed to migrate %s entity ID for %s: %s -> %s (%s)",
                platform,
                unique_id,
                current_entity_id,
                target_entity_id,
                err,
            )


# Options that can be updated without requiring a full reload
# These are applied immediately via coordinator.config and decision_engine.refresh_settings()
LIVE_UPDATE_OPTIONS = {
    CONF_ARBITRAGE_MODE_DEADLINE_HOUR,
    CONF_ARBITRAGE_MODE_RESERVE_SOC,
    CONF_MAX_SOC_THRESHOLD,
    CONF_MAX_SOC_THRESHOLD_SUNNY,
    CONF_SOLAR_FORECAST_START_HOUR,
    CONF_SUNNY_FORECAST_THRESHOLD_KWH,
}


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle an options update.

    For options in LIVE_UPDATE_OPTIONS, skip reload as they're updated in-place.
    For other options (entity selections, etc.), perform a full reload.
    """
    coordinator: ElectricityPlannerCoordinator = hass.data[DOMAIN].get(entry.entry_id)
    if coordinator is None:
        await hass.config_entries.async_reload(entry.entry_id)
        return

    # Check if only live-updatable options changed
    old_config = coordinator.config
    new_options = dict(entry.data)
    if entry.options:
        new_options.update(entry.options)

    # Find what changed
    changed_keys = set()
    for key in set(old_config.keys()) | set(new_options.keys()):
        if old_config.get(key) != new_options.get(key):
            changed_keys.add(key)

    # No effective diff (often because live options were already applied in-place)
    # -> nothing to reload.
    if not changed_keys:
        _LOGGER.debug("Skipping reload - no effective config changes detected")
        return

    # If only live-update options changed, skip reload
    if changed_keys.issubset(LIVE_UPDATE_OPTIONS):
        # Apply merged config immediately so live-update options take effect
        # without requiring a full integration reload.
        coordinator.config = new_options
        coordinator.decision_engine.refresh_settings(coordinator.config)
        await coordinator.async_request_refresh()
        _LOGGER.debug(
            "Skipping reload - only live-update options changed: %s", changed_keys
        )
        return

    # Full reload for other changes
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
        action = call.data.get(ATTR_ACTION)
        reason = call.data.get(ATTR_REASON)
        duration_minutes = call.data.get(ATTR_DURATION)
        duration = timedelta(minutes=duration_minutes) if duration_minutes is not None else None
        charger_limit = call.data.get(ATTR_CHARGER_LIMIT_OVERRIDE)
        grid_setpoint = call.data.get(ATTR_GRID_SETPOINT_OVERRIDE)

        if target == MANUAL_OVERRIDE_TARGET_ARBITRAGE_MODE:
            invalid_fields = [
                field_name
                for field_name, field_value in (
                    ("action", action),
                    ("duration", duration_minutes),
                    ("charger_limit", charger_limit),
                    ("grid_setpoint", grid_setpoint),
                )
                if field_value is not None
            ]
            if invalid_fields:
                raise HomeAssistantError(
                    "arbitrage_mode target does not accept "
                    + ", ".join(invalid_fields)
                )
            await coordinator.async_set_arbitrage_mode(reason=reason)
            await coordinator.async_request_refresh()
            return

        if target == MANUAL_OVERRIDE_TARGET_NEGATIVE_BUY:
            invalid_fields = [
                field_name
                for field_name, field_value in (
                    ("action", action),
                    ("duration", duration_minutes),
                    ("charger_limit", charger_limit),
                    ("grid_setpoint", grid_setpoint),
                )
                if field_value is not None
            ]
            if invalid_fields:
                raise HomeAssistantError(
                    "negative_buy target does not accept "
                    + ", ".join(invalid_fields)
                )
            await coordinator.async_set_negative_buy_mode(reason=reason)
            await coordinator.async_request_refresh()
            return

        # For numeric-only targets, action is optional
        # For boolean targets (battery/car/both), action is required
        if target in (MANUAL_OVERRIDE_TARGET_CHARGER_LIMIT, MANUAL_OVERRIDE_TARGET_GRID_SETPOINT):
            if target == MANUAL_OVERRIDE_TARGET_CHARGER_LIMIT and charger_limit is None:
                raise HomeAssistantError(
                    "charger_limit is required when target is charger_limit"
                )
            if target == MANUAL_OVERRIDE_TARGET_GRID_SETPOINT and grid_setpoint is None:
                raise HomeAssistantError(
                    "grid_setpoint is required when target is grid_setpoint"
                )
            # Numeric-only override, no boolean value needed
            value = None
        elif action is None:
            raise HomeAssistantError(
                f"Action is required when target is {target}. "
                f"Use 'force_charge' or 'force_wait'."
            )
        else:
            value = action == MANUAL_OVERRIDE_ACTION_FORCE_CHARGE

        await coordinator.async_set_manual_override(
            target, value, duration, reason, charger_limit, grid_setpoint
        )
        await coordinator.async_request_refresh()

    async def _async_handle_clear_override(call):
        entry_id = _resolve_entry_id(call.data.get(ATTR_ENTRY_ID))
        coordinator: ElectricityPlannerCoordinator | None = hass.data.get(DOMAIN, {}).get(entry_id)

        if not coordinator:
            raise HomeAssistantError(f"Coordinator for entry {entry_id} is no longer available")

        target = call.data.get(ATTR_TARGET, MANUAL_OVERRIDE_TARGET_ALL)
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
