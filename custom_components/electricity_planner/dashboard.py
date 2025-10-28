"""Managed Lovelace dashboard support for Electricity Planner.

Each config entry gets its own Lovelace dashboard stored in Home Assistant's
storage-backed collection. The dashboard mirrors the bundled YAML template but
is rewritten with the entity ids that the user actually configured, so every
installation gets a working view without manual edits.

The generated dashboard expects these HACS cards to be installed:
- gauge-card-pro – price gauges
- apexcharts-card – historical charts
- button-card – manual override buttons
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import Any

import voluptuous as vol
import yaml

from homeassistant.components import frontend
from homeassistant.components.lovelace import const as ll_const
from homeassistant.components.lovelace import dashboard as ll_dashboard
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

MANAGED_KEY = "electricity_planner_managed"
MANAGED_VERSION = 1
TEMPLATE_FILENAME = "dashboard_template.yaml"

ENTITY_WAIT_TIMEOUT = 30
ENTITY_WAIT_INTERVAL = 1.0

PENDING_SET_KEY = "dashboard_pending"


@dataclass(slots=True)
class EntityReference:
    """Mapping between template placeholders and unique ID suffixes."""

    placeholder: str
    unique_suffix: str


ENTITY_REFERENCES: tuple[EntityReference, ...] = (
    EntityReference("binary_sensor.electricity_planner_battery_charge_from_grid", "battery_grid_charging"),
    EntityReference("binary_sensor.electricity_planner_car_charge_from_grid", "car_grid_charging"),
    EntityReference("binary_sensor.electricity_planner_data_nord_pool_available", "data_availability"),
    EntityReference("binary_sensor.electricity_planner_price_below_threshold", "low_price"),
    EntityReference("binary_sensor.electricity_planner_solar_producing_power", "solar_production"),
    EntityReference("binary_sensor.electricity_planner_solar_feed_in_grid", "feedin_solar"),
    EntityReference("binary_sensor.solar_feed_in_grid", "feedin_solar"),
    EntityReference("sensor.electricity_planner_current_electricity_price", "price_analysis"),
    EntityReference("sensor.electricity_planner_decision_diagnostics", "decision_diagnostics"),
    EntityReference("sensor.electricity_planner_decision_diagnostics_diagnostic", "decision_diagnostics"),
    EntityReference("sensor.electricity_planner_battery_soc_average", "battery_analysis"),
    EntityReference("sensor.electricity_planner_car_charger_limit", "charger_limit"),
    EntityReference("sensor.electricity_planner_grid_setpoint", "grid_setpoint"),
    EntityReference("sensor.electricity_planner_solar_surplus_power", "power_analysis"),
    EntityReference("sensor.electricity_planner_data_unavailable_duration", "data_unavailable_duration"),
    EntityReference("sensor.electricity_planner_diagnostics_monitoring_current_feed_in_price", "feedin_price"),
    EntityReference("sensor.electricity_planner_diagnostics_monitoring_feed_in_price_threshold", "feedin_price_threshold"),
    EntityReference("sensor.electricity_planner_diagnostics_monitoring_price_threshold", "price_threshold"),
    EntityReference("sensor.electricity_planner_diagnostics_monitoring_price_forecast_insights", "forecast_insights"),
    EntityReference("sensor.electricity_planner_diagnostics_monitoring_nord_pool_prices", "nordpool_prices"),
    EntityReference("sensor.electricity_planner_diagnostics_monitoring_significant_solar_threshold", "significant_solar_threshold"),
    EntityReference("sensor.electricity_planner_diagnostics_monitoring_very_low_price_threshold", "very_low_price_threshold"),
    EntityReference("sensor.electricity_planner_diagnostics_monitoring_emergency_soc_threshold", "emergency_soc_threshold"),
    EntityReference("switch.electricity_planner_car_permissive_mode", "car_permissive_mode"),
)

CORE_ENTITY_SUFFIXES: tuple[str, ...] = (
    "battery_grid_charging",
    "car_grid_charging",
    "price_analysis",
)


@dataclass(slots=True)
class DashboardHandles:
    """Wires into Lovelace storage mode internals."""

    collection: ll_dashboard.DashboardsCollection
    dashboards: dict[str, ll_dashboard.LovelaceStorage]


async def async_setup_or_update_dashboard(hass: HomeAssistant, entry) -> None:
    """Create or update the managed dashboard for the given config entry."""
    _LOGGER.info("Starting dashboard creation for entry: %s (title: %s)", entry.entry_id, entry.title)

    handles = _get_lovelace_handles(hass)
    if handles is None:
        _LOGGER.warning("Lovelace handles not available, scheduling retry")
        _maybe_schedule_retry(hass, entry)
        return

    _LOGGER.debug("Lovelace handles retrieved successfully")

    # Load the dashboards collection if needed
    try:
        if hasattr(handles.collection, 'async_load'):
            await handles.collection.async_load()
            _LOGGER.debug("DashboardsCollection loaded successfully")
    except Exception as err:
        _LOGGER.error("Failed to load DashboardsCollection: %s", err, exc_info=True)
        return

    try:
        entity_map = await _async_wait_for_entity_map(hass, entry)
        _LOGGER.debug("Entity map built with %d entities", len(entity_map))
    except asyncio.TimeoutError:
        _LOGGER.warning("Timed out waiting for entities before building dashboard for %s", entry.entry_id)
        entity_map = _build_entity_map(hass, entry)
        _LOGGER.debug("Fallback entity map built with %d entities", len(entity_map))

    if not entity_map:
        _LOGGER.warning("No registered entities for %s; skipping dashboard creation", entry.entry_id)
        return

    template_text = _load_template_text()
    if not template_text:
        _LOGGER.error("Dashboard template %s missing; skipping creation", TEMPLATE_FILENAME)
        return

    _LOGGER.debug("Dashboard template loaded successfully (%d chars)", len(template_text))

    replacements = _build_replacements(entry, entity_map)
    _LOGGER.debug("Built %d entity replacements", len(replacements))
    rendered_template = _apply_replacements(template_text, replacements)

    try:
        dashboard_config = yaml.safe_load(rendered_template)
        _LOGGER.debug("Dashboard template parsed successfully")
    except yaml.YAMLError as error:
        _LOGGER.error("Failed to parse dashboard template: %s", error)
        return

    if not isinstance(dashboard_config, dict) or "views" not in dashboard_config:
        _LOGGER.error("Rendered dashboard template invalid; skipping (type=%s, has_views=%s)",
                     type(dashboard_config), "views" in dashboard_config if isinstance(dashboard_config, dict) else False)
        return

    dashboard_config[MANAGED_KEY] = {
        "entry_id": entry.entry_id,
        "version": MANAGED_VERSION,
    }

    url_path = _dashboard_url_path(entry)
    _LOGGER.debug("Dashboard URL path: %s", url_path)

    try:
        storage = await _ensure_dashboard_record(hass, handles, entry, url_path)
    except Exception as err:
        _LOGGER.error("Exception in _ensure_dashboard_record: %s", err, exc_info=True)
        return

    if storage is None:
        _LOGGER.error("Failed to ensure dashboard record - storage is None")
        return

    _LOGGER.info("Dashboard record created successfully, storage: %s", type(storage))

    _LOGGER.debug("Saving dashboard configuration...")
    await _save_dashboard(storage, dashboard_config)

    # Register the dashboard panel in the frontend (CRITICAL for sidebar visibility)
    title = entry.title or "Electricity Planner"
    _register_dashboard_panel(hass, url_path, title, storage.config)

    _LOGGER.info("Dashboard setup completed for entry: %s", entry.entry_id)


async def async_remove_dashboard(hass: HomeAssistant, entry) -> None:
    """Remove the managed dashboard when the config entry is unloaded."""
    handles = _get_lovelace_handles(hass)
    if handles is None:
        return

    url_path = _dashboard_url_path(entry)
    storage = handles.dashboards.get(url_path)
    if storage is None or storage.config is None:
        return

    try:
        existing_config = await storage.async_load(False)
    except ll_dashboard.ConfigNotFound:
        existing_config = None
    except HomeAssistantError as error:
        _LOGGER.debug("Unable to inspect dashboard %s during removal: %s", url_path, error)
        return

    is_managed = (
        existing_config is not None
        and existing_config.get(MANAGED_KEY, {}).get("entry_id") == entry.entry_id
    )
    if not is_managed:
        return

    item_id = storage.config.get("id")
    if not item_id:
        return

    try:
        await handles.collection.async_delete_item(item_id)
    except (vol.Invalid, HomeAssistantError) as error:
        _LOGGER.debug("Unable to delete dashboard %s: %s", url_path, error)


async def _ensure_dashboard_record(
    hass: HomeAssistant,
    handles: DashboardHandles,
    entry,
    url_path: str,
) -> ll_dashboard.LovelaceStorage | None:
    """Create or update the Lovelace storage entry metadata."""
    title = entry.title or "Electricity Planner"
    create_data = {
        ll_const.CONF_URL_PATH: url_path,
        ll_const.CONF_TITLE: title,
        ll_const.CONF_ICON: "mdi:lightning-bolt",
        ll_const.CONF_REQUIRE_ADMIN: False,
        ll_const.CONF_SHOW_IN_SIDEBAR: True,
    }

    # Add CONF_ALLOW_SINGLE_WORD only if it exists (added in newer HA versions)
    if hasattr(ll_const, 'CONF_ALLOW_SINGLE_WORD'):
        create_data[ll_const.CONF_ALLOW_SINGLE_WORD] = True

    # Check if dashboard already exists in the collection
    dashboard_item: dict[str, Any] | None = None
    existing_item_id: str | None = None

    try:
        collection_data = handles.collection.data
        _LOGGER.debug("Collection has %d items", len(collection_data))
    except Exception as err:
        _LOGGER.error("Failed to access collection.data: %s", err, exc_info=True)
        return None

    for item in collection_data.values():
        if item.get(ll_const.CONF_URL_PATH) == url_path:
            dashboard_item = item
            existing_item_id = item.get("id")
            _LOGGER.debug("Found existing dashboard item with id=%s", existing_item_id)
            break

    created_item = False
    if dashboard_item is None:
        try:
            _LOGGER.debug("Creating new dashboard with url_path=%s, title=%s", url_path, title)
            _LOGGER.debug("Create data: %s", create_data)
            dashboard_item = await handles.collection.async_create_item(create_data)
            created_item = True
            existing_item_id = dashboard_item.get("id")
            _LOGGER.info("Successfully created dashboard item: %s (id=%s)", title, existing_item_id)
        except vol.Invalid as error:
            _LOGGER.error("Validation error creating dashboard %s: %s", url_path, error, exc_info=True)
            return None
        except HomeAssistantError as error:
            _LOGGER.error("Home Assistant error creating dashboard %s: %s", url_path, error, exc_info=True)
            return None
        except Exception as error:
            _LOGGER.error("Unexpected error creating dashboard %s: %s", url_path, error, exc_info=True)
            return None
    else:
        # Dashboard item exists, check if we need to update metadata
        desired_fields = {
            ll_const.CONF_TITLE: title,
            ll_const.CONF_ICON: "mdi:lightning-bolt",
            ll_const.CONF_REQUIRE_ADMIN: False,
            ll_const.CONF_SHOW_IN_SIDEBAR: True,
        }
        updates: dict[str, Any] = {
            key: value
            for key, value in desired_fields.items()
            if dashboard_item.get(key) != value
        }
        if updates and existing_item_id:
            try:
                dashboard_item = await handles.collection.async_update_item(existing_item_id, updates)
                _LOGGER.debug("Updated dashboard metadata for %s: %s", url_path, updates)
            except Exception as error:
                _LOGGER.warning("Unable to update dashboard metadata for %s: %s", url_path, error)

    if dashboard_item is None:
        _LOGGER.error("dashboard_item is None after create/update")
        return None

    # Create or get the LovelaceStorage object
    existing_dashboard = handles.dashboards.get(url_path)
    if isinstance(existing_dashboard, ll_dashboard.LovelaceStorage):
        existing_dashboard.config = dashboard_item
        storage = existing_dashboard
        _LOGGER.debug("Updated existing LovelaceStorage for %s", url_path)
    else:
        storage = ll_dashboard.LovelaceStorage(hass, dashboard_item)
        handles.dashboards[url_path] = storage
        _LOGGER.debug("Created new LovelaceStorage for %s", url_path)

    return storage


async def _save_dashboard(storage: ll_dashboard.LovelaceStorage, config: dict[str, Any]) -> None:
    """Persist the Lovelace dashboard if it changed."""
    try:
        existing = await storage.async_load(False)
        _LOGGER.debug("Loaded existing dashboard config for comparison")
    except ll_dashboard.ConfigNotFound:
        _LOGGER.debug("No existing dashboard config found, will create new")
        existing = None
    except HomeAssistantError as error:
        _LOGGER.warning("Unable to load existing dashboard state: %s", error)
        existing = None

    if existing is not None and _configs_equal(existing, config):
        _LOGGER.debug("Dashboard config unchanged, skipping save")
        return

    try:
        _LOGGER.debug("Saving dashboard config with %d views", len(config.get("views", [])))
        await storage.async_save(config)
        _LOGGER.info("Dashboard config saved successfully")
    except HomeAssistantError as error:
        _LOGGER.error("Failed to write managed dashboard: %s", error)


def _register_dashboard_panel(
    hass: HomeAssistant,
    url_path: str,
    title: str,
    dashboard_config: dict[str, Any] | None,
) -> None:
    """Register the dashboard as a frontend panel (makes it visible in sidebar)."""
    if dashboard_config is None:
        _LOGGER.warning("Cannot register panel: dashboard_config is None")
        return

    panel_kwargs = {
        "frontend_url_path": url_path,
        "require_admin": dashboard_config.get(ll_const.CONF_REQUIRE_ADMIN, False),
        "config": {"mode": ll_const.MODE_STORAGE},
    }

    # Add sidebar info if dashboard should be visible
    if dashboard_config.get(ll_const.CONF_SHOW_IN_SIDEBAR, True):
        panel_kwargs["sidebar_title"] = dashboard_config.get(ll_const.CONF_TITLE, title)
        panel_kwargs["sidebar_icon"] = dashboard_config.get(ll_const.CONF_ICON, "mdi:lightning-bolt")

    try:
        _LOGGER.debug("Registering frontend panel for %s", url_path)
        frontend.async_register_built_in_panel(
            hass,
            ll_const.DOMAIN,
            **panel_kwargs,
        )
        _LOGGER.info("Successfully registered dashboard panel in sidebar: %s", title)
    except ValueError as err:
        # Panel already registered (e.g., from previous setup)
        _LOGGER.debug("Panel registration skipped for %s (already exists): %s", url_path, err)
    except Exception as err:
        _LOGGER.error("Failed to register dashboard panel for %s: %s", url_path, err)


def _get_lovelace_handles(hass: HomeAssistant) -> DashboardHandles | None:
    """Return Lovelace storage handles when running in storage mode."""
    # Use LOVELACE_DATA constant (not DOMAIN) to get the lovelace data structure
    lovelace_data_key = getattr(ll_const, 'LOVELACE_DATA', 'lovelace')
    lovelace_data = hass.data.get(lovelace_data_key)

    if not lovelace_data:
        _LOGGER.debug("Lovelace data not found at key '%s' (HA may not be fully started or Lovelace not in storage mode)", lovelace_data_key)
        return None

    _LOGGER.debug("Found lovelace_data with keys: %s", list(lovelace_data.keys()) if isinstance(lovelace_data, dict) else type(lovelace_data))

    # Create a new DashboardsCollection instance (as HeatingControl does)
    try:
        collection = ll_dashboard.DashboardsCollection(hass)
        _LOGGER.debug("Created DashboardsCollection instance")
    except Exception as err:
        _LOGGER.error("Failed to create DashboardsCollection: %s", err, exc_info=True)
        return None

    # Get the dashboards dict from lovelace_data (use property access, not dict .get())
    try:
        dashboards = lovelace_data.dashboards
        _LOGGER.debug("Found %d existing dashboards", len(dashboards))
    except AttributeError:
        # Fallback for older HA versions
        _LOGGER.debug("Using dict access fallback for dashboards")
        dashboards = lovelace_data.get("dashboards", {})

    return DashboardHandles(collection=collection, dashboards=dashboards)


def _maybe_schedule_retry(hass: HomeAssistant, entry) -> None:
    """Retry once Home Assistant finishes starting if Lovelace is not ready yet."""
    if hass.is_running:
        _LOGGER.debug("Lovelace storage not available; dashboard creation skipped for %s", entry.entry_id)
        return

    data = hass.data.setdefault(DOMAIN, {})
    pending: set[str] = data.setdefault(PENDING_SET_KEY, set())
    if entry.entry_id in pending:
        return
    pending.add(entry.entry_id)

    @callback
    def _retry_later(_event) -> None:
        pending.discard(entry.entry_id)
        hass.async_create_task(async_setup_or_update_dashboard(hass, entry))

    hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, _retry_later)


async def _async_wait_for_entity_map(hass: HomeAssistant, entry) -> dict[str, str]:
    """Poll the entity registry until key entities for this entry exist."""
    end = hass.loop.time() + ENTITY_WAIT_TIMEOUT
    while True:
        entity_map = _build_entity_map(hass, entry)
        if all(f"{entry.entry_id}_{suffix}" in entity_map for suffix in CORE_ENTITY_SUFFIXES):
            return entity_map
        if hass.loop.time() >= end:
            raise asyncio.TimeoutError
        await asyncio.sleep(ENTITY_WAIT_INTERVAL)


def _build_entity_map(hass: HomeAssistant, entry) -> dict[str, str]:
    """Return a mapping of unique_id -> entity_id for the config entry."""
    registry = er.async_get(hass)
    mapped: dict[str, str] = {}
    for entity_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity_entry.unique_id and entity_entry.entity_id:
            mapped[entity_entry.unique_id] = entity_entry.entity_id
    return mapped


def _build_replacements(entry, entity_map: dict[str, str]) -> dict[str, str]:
    """Create replacement map from template placeholders to real entity IDs."""
    replacements: dict[str, str] = {}
    for ref in ENTITY_REFERENCES:
        unique_id = f"{entry.entry_id}_{ref.unique_suffix}"
        entity_id = entity_map.get(unique_id)
        if entity_id:
            replacements[ref.placeholder] = entity_id
    return replacements


def _dashboard_url_path(entry) -> str:
    """Return a deterministic dashboard URL path for the entry."""
    slug = slugify(entry.title or "")
    if not slug:
        slug = "planner"
    suffix = entry.entry_id[:6]
    return f"electricity-planner-{slug}-{suffix}"


def _configs_equal(config_a: dict[str, Any], config_b: dict[str, Any]) -> bool:
    """Return True when two dashboard configs are logically equivalent."""
    return json.dumps(config_a, sort_keys=True) == json.dumps(config_b, sort_keys=True)


@lru_cache(maxsize=1)
def _load_template_text() -> str:
    """Load the bundled dashboard template."""
    try:
        template_path = resources.files(__package__) / TEMPLATE_FILENAME
    except FileNotFoundError:
        return ""

    try:
        return template_path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return ""


def _apply_replacements(template: str, replacements: dict[str, str]) -> str:
    """Apply entity replacements to the raw template text."""
    rendered = template
    for placeholder, entity_id in replacements.items():
        rendered = rendered.replace(placeholder, entity_id)
    return rendered
