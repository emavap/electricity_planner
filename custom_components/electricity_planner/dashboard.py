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
    handles = _get_lovelace_handles(hass)
    if handles is None:
        _maybe_schedule_retry(hass, entry)
        return

    try:
        entity_map = await _async_wait_for_entity_map(hass, entry)
    except asyncio.TimeoutError:
        _LOGGER.debug("Timed out waiting for entities before building dashboard for %s", entry.entry_id)
        entity_map = _build_entity_map(hass, entry)

    if not entity_map:
        _LOGGER.debug("No registered entities for %s; skipping dashboard creation", entry.entry_id)
        return

    template_text = _load_template_text()
    if not template_text:
        _LOGGER.debug("Dashboard template %s missing; skipping creation", TEMPLATE_FILENAME)
        return

    replacements = _build_replacements(entry, entity_map)
    rendered_template = _apply_replacements(template_text, replacements)

    try:
        dashboard_config = yaml.safe_load(rendered_template)
    except yaml.YAMLError as error:
        _LOGGER.warning("Failed to parse dashboard template: %s", error)
        return

    if not isinstance(dashboard_config, dict) or "views" not in dashboard_config:
        _LOGGER.debug("Rendered dashboard template invalid; skipping")
        return

    dashboard_config[MANAGED_KEY] = {
        "entry_id": entry.entry_id,
        "version": MANAGED_VERSION,
    }

    url_path = _dashboard_url_path(entry)
    storage = await _ensure_dashboard_record(hass, handles, entry, url_path)
    if storage is None:
        return

    await _save_dashboard(storage, dashboard_config)


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
        ll_const.CONF_ALLOW_SINGLE_WORD: True,
    }

    storage = handles.dashboards.get(url_path)
    if storage is None:
        try:
            await handles.collection.async_create_item(create_data)
        except (vol.Invalid, HomeAssistantError) as error:
            _LOGGER.warning("Unable to register managed dashboard %s: %s", url_path, error)
            return None
        storage = handles.dashboards.get(url_path)
        if storage is None:
            _LOGGER.debug("Loveless storage not registered yet for %s", url_path)
            return None
    elif storage.config:
        item_id = storage.config.get("id")
        updates: dict[str, Any] = {}
        for field in (
            ll_const.CONF_TITLE,
            ll_const.CONF_ICON,
            ll_const.CONF_REQUIRE_ADMIN,
            ll_const.CONF_SHOW_IN_SIDEBAR,
        ):
            if storage.config.get(field) != create_data[field]:
                updates[field] = create_data[field]
        if updates and item_id:
            try:
                await handles.collection.async_update_item(item_id, updates)
            except (vol.Invalid, HomeAssistantError) as error:
                _LOGGER.debug("Unable to update dashboard metadata for %s: %s", url_path, error)

    return storage


async def _save_dashboard(storage: ll_dashboard.LovelaceStorage, config: dict[str, Any]) -> None:
    """Persist the Lovelace dashboard if it changed."""
    try:
        existing = await storage.async_load(False)
    except ll_dashboard.ConfigNotFound:
        existing = None
    except HomeAssistantError as error:
        _LOGGER.debug("Unable to load existing dashboard state: %s", error)
        existing = None

    if existing is not None and _configs_equal(existing, config):
        return

    try:
        await storage.async_save(config)
    except HomeAssistantError as error:
        _LOGGER.warning("Failed to write managed dashboard: %s", error)


def _get_lovelace_handles(hass: HomeAssistant) -> DashboardHandles | None:
    """Return Lovelace storage handles when running in storage mode."""
    lovelace_data = hass.data.get(ll_const.DOMAIN)
    if not lovelace_data:
        return None

    collection = lovelace_data.get("dashboards_collection")
    dashboards = lovelace_data.get("dashboards")

    if collection is None or dashboards is None:
        return None

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
